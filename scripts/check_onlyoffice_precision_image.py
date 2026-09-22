"""Isolated image startup/restart smoke test; never mounts production volumes.

Requires a locally built candidate image and Docker. Publishes only an ephemeral
loopback port. Records decoded HTTP/FS SDK hashes and readiness time, not PPTX
acceptance or peak-memory benchmarks. Always removes its own container/volumes.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True, timeout=120).strip()


def check(image: str, output: Path, timeout: float) -> None:
    output.mkdir(parents=True, exist_ok=False)
    name = "mai-precision-image-check-" + secrets.token_hex(6)
    image_id = docker("image", "inspect", image, "--format", "{{.Id}}")
    report: dict[str, object] = {"image": image, "image_id": image_id, "cycles": []}
    cycles: list[dict[str, object]] = []
    report["cycles"] = cycles
    try:
        for cycle in ("cold", "restart"):
            start = time.monotonic()
            if cycle == "cold":
                docker(
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--shm-size",
                    "256m",
                    "-p",
                    "127.0.0.1::80",
                    "-e",
                    "JWT_ENABLED=true",
                    "-e",
                    "JWT_SECRET=" + secrets.token_hex(32),
                    image_id,
                )
            else:
                docker("restart", "-t", "20", name)
            port = docker("port", name, "80/tcp").rsplit(":", 1)[1]
            base = "http://127.0.0.1:" + port
            deadline = start + timeout
            while True:
                try:
                    with urllib.request.urlopen(base + "/healthcheck", timeout=3) as response:
                        if response.read().strip() == b"true":
                            break
                except (OSError, urllib.error.URLError):
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{cycle}: DocumentServer not ready")
                time.sleep(1)
            elapsed = time.monotonic() - start
            manifest = json.loads(
                docker(
                    "exec",
                    name,
                    "python3",
                    "/opt/mai-precision/patch.py",
                    "--verify-install",
                    "/var/www/onlyoffice/documentserver/sdkjs/slide",
                )
            )
            hashes = {}
            for encoding in ("identity", "gzip"):
                request = urllib.request.Request(
                    base + "/sdkjs/slide/sdk-all.js", headers={"Accept-Encoding": encoding}
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = response.read()
                    if response.headers.get("Content-Encoding") == "gzip":
                        data = gzip.decompress(data)
                digest = hashlib.sha256(data).hexdigest()
                if digest != manifest["patched_sha256"]:
                    raise ValueError(f"{cycle}: HTTP SDK mismatch ({encoding})")
                hashes[encoding] = digest
            cycles.append(
                {
                    "cycle": cycle,
                    "readiness_seconds": elapsed,
                    "manifest": manifest,
                    "http_sha256": hashes,
                    "stats": docker("stats", "--no-stream", "--format", "{{json .}}", name),
                }
            )
        report["passed"] = True
    finally:
        try:
            (output / "report.json").write_text(json.dumps(report, indent=2))
            with (output / "container.log").open("w") as log:
                subprocess.run(["docker", "logs", name], stdout=log, stderr=log, timeout=30)
        finally:
            # The random name belongs exclusively to this run. Anonymous volumes only.
            docker("rm", "-f", "-v", name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    check(args.image, args.out, args.timeout)
