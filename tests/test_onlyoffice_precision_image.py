"""Image smoke harness never uses shared volumes and cleans up on failures."""

import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "precision_image",
    Path(__file__).resolve().parents[1] / "scripts/check_onlyoffice_precision_image.py",
)
assert spec and spec.loader
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def harness(monkeypatch, *, mismatch=False, unhealthy=False, log_failure=False):
    calls = []
    sdk = b"verified SDK"

    def docker(*args):
        calls.append(args)
        if args[0] == "image":
            return "sha256:pinned"
        if args[0] == "port":
            return "127.0.0.1:12345"
        if args[0] == "exec":
            return json.dumps({"patched_sha256": hashlib.sha256(sdk).hexdigest()})
        return "{}"

    def urlopen(request, **kwargs):
        if isinstance(request, str):
            response = io.BytesIO(b"false" if unhealthy else b"true")
            response.headers = {}
        else:
            data = b"wrong SDK" if mismatch else sdk
            compressed = request.get_header("Accept-encoding") == "gzip"
            response = io.BytesIO(gzip.compress(data) if compressed else data)
            response.headers = {"Content-Encoding": "gzip"} if compressed else {}
        return response

    def logs(*args, **kwargs):
        if log_failure:
            raise TimeoutError("logs timed out")

    monkeypatch.setattr(smoke, "docker", docker)
    monkeypatch.setattr(smoke.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(smoke.subprocess, "run", logs)
    return calls


def test_cold_restart_http_hashes_and_cleanup(tmp_path, monkeypatch):
    calls = harness(monkeypatch)
    smoke.check("candidate", tmp_path / "smoke", 10)
    report = json.loads((tmp_path / "smoke/report.json").read_text())
    assert report["passed"]
    assert [c["cycle"] for c in report["cycles"]] == ["cold", "restart"]
    for cycle in report["cycles"]:
        assert set(cycle["http_sha256"]) == {"identity", "gzip"}
        assert len(set(cycle["http_sha256"].values())) == 1
    run = next(c for c in calls if c[0] == "run")
    assert "127.0.0.1::80" in run and "-v" not in run and "--mount" not in run
    assert run[-1] == "sha256:pinned"
    assert calls[-1][:3] == ("rm", "-f", "-v")
    with pytest.raises(FileExistsError):
        smoke.check("candidate", tmp_path / "smoke", 10)


@pytest.mark.parametrize("failure", ["mismatch", "unhealthy", "log_failure"])
def test_failure_still_removes_container_and_volumes(tmp_path, monkeypatch, failure):
    calls = harness(monkeypatch, **{failure: True})
    with pytest.raises((ValueError, TimeoutError)):
        smoke.check("candidate", tmp_path / "smoke", 0)
    assert calls[-1][:3] == ("rm", "-f", "-v")
    report = json.loads((tmp_path / "smoke/report.json").read_text())
    if failure != "log_failure":
        assert not report.get("passed", False)
