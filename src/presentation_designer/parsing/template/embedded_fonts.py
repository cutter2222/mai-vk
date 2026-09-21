"""Relationship-based PPTX font extraction into quarantine, never global registration."""

from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import posixpath
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from typing import Any
from urllib.parse import unquote, urlsplit

from lxml import etree

from presentation_designer.parsing.template import font_worker

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
MAX_XML = 4 * 1024**2
MAX_FACES = 32
MAX_TOTAL = 64 * 1024**2
SCHEMA = 1


def _read(zf: zipfile.ZipFile, name: str, limit: int) -> bytes:
    if zf.getinfo(name).file_size > limit:
        raise ValueError("part exceeds size limit")
    return zf.read(name)


def _xml(zf: zipfile.ZipFile, name: str) -> Any:
    root = etree.fromstring(
        _read(zf, name, MAX_XML),
        parser=etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
        ),
    )
    if root.getroottree().docinfo.doctype:
        raise ValueError("DTD is not allowed")
    return root


def _target(target: str) -> str:
    target = unquote(target)
    uri = urlsplit(target)
    if (
        uri.scheme
        or uri.netloc
        or uri.query
        or uri.fragment
        or "\\" in target
        or any(ord(c) < 32 for c in target)
    ):
        raise ValueError("unsafe relationship")
    name = posixpath.normpath(
        target.lstrip("/") if target.startswith("/") else posixpath.join("ppt", target)
    )
    if name in (".", "..") or name.startswith("../"):
        raise ValueError("relationship escapes package")
    return name


def _decode(data: bytes, work: pathlib.Path, decoder: str, timeout: float) -> dict[str, Any]:
    work = work.resolve()
    source = work / "input.bin"
    source.write_bytes(data)
    process = subprocess.Popen(
        [sys.executable, str(pathlib.Path(font_worker.__file__)), str(source), decoder],
        cwd=work,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={"PATH": os.defpath, "HOME": str(work), "LANG": "C.UTF-8"},
    )
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        return {"status": "decode_timeout"}
    result = source.with_suffix(".json")
    if process.returncode or not result.is_file() or result.stat().st_size > MAX_XML:
        return {"status": "decode_failed"}
    return json.loads(result.read_text())  # type: ignore[no-any-return]


def extract(
    pptx: pathlib.Path | bytes, destination: pathlib.Path, *, decoder: str = ""
) -> dict[str, Any]:
    """Write validated content-addressed faces and a report. Caller owns destination.

    No claim of completeness can be made for raw fonts or unflagged EOT subsets;
    every extracted face requires review before any renderer registration.
    """
    destination.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "ok",
        "fonts": entries,
        "registration": "not_registered",
        "renderer_availability": "not_checked",
    }
    deadline = time.monotonic() + 20
    try:
        with zipfile.ZipFile(io.BytesIO(pptx) if isinstance(pptx, bytes) else pptx) as zf:
            names = zf.namelist()
            if len(names) > 20000 or len(names) != len(set(names)):
                raise ValueError("too many or duplicate ZIP entries")
            root = _xml(zf, "ppt/presentation.xml")
            faces = root.findall(f"{{{P}}}embeddedFontLst/{{{P}}}embeddedFont")
            if not faces:
                return report
            rels = _xml(zf, "ppt/_rels/presentation.xml.rels")
            relations = {}
            for rel in rels.findall(f"{{{PKG}}}Relationship"):
                rid = rel.get("Id")
                if not rid or rid in relations:
                    raise ValueError("ambiguous relationship id")
                relations[rid] = rel
            total = 0
            identities: dict[tuple[str, str], str] = {}
            hashes: set[str] = set()
            for font in faces:
                name = font.find(f"{{{P}}}font")
                family = name.get("typeface", "") if name is not None else ""
                for face in font:
                    style = etree.QName(face).localname
                    if face.tag not in {
                        f"{{{P}}}{s}"
                        for s in (
                            "regular",
                            "bold",
                            "italic",
                            "boldItalic",
                        )
                    }:
                        continue
                    if len(entries) >= MAX_FACES or time.monotonic() >= deadline:
                        report["status"] = "resource_limit"
                        return report
                    entry: dict[str, Any] = {
                        "declared_family": family,
                        "face": style,
                        "relationship_id": face.get(f"{{{R}}}id"),
                        "status": "invalid_relationship",
                    }
                    entries.append(entry)
                    rel = relations.get(entry["relationship_id"])
                    if rel is None:
                        continue
                    if rel.get("TargetMode", "Internal") != "Internal":
                        entry["status"] = "external_relationship"
                        continue
                    if rel.get("Type") != f"{R}/font":
                        continue
                    try:
                        part = _target(rel.get("Target", ""))
                        entry["part"] = part
                        size = zf.getinfo(part).file_size
                        if size > font_worker.MAX_FONT or total + size > MAX_TOTAL:
                            entry["status"] = "size_limit"
                            continue
                        total += size
                        data = _read(zf, part, font_worker.MAX_FONT)
                    except (ValueError, KeyError):
                        continue
                    with tempfile.TemporaryDirectory(dir=destination, prefix=".decode-") as tmp:
                        work = pathlib.Path(tmp)
                        info = _decode(
                            data, work, decoder, max(0.01, min(8, deadline - time.monotonic()))
                        )
                        entry.update(info)
                        if info["status"] != "validated":
                            if (
                                part.lower().endswith(".odttf")
                                and info.get("source_format") == "unknown"
                            ):
                                entry["source_format"] = "odttf_unverified"
                            continue
                        digest = info["sha256"]
                        filename = f"{digest}.{info['format']}"
                        shutil.copyfile(work / "input.font", destination / filename)
                        entry.update(
                            file=filename, status="pending_review", coverage="unknown_fullness"
                        )
                        identity = (info["family"].casefold(), style)
                        expected = (
                            style in ("bold", "boldItalic"),
                            style in ("italic", "boldItalic"),
                        )
                        if info["family"].casefold() != family.casefold() or expected != (
                            info["bold"],
                            info["italic"],
                        ):
                            entry["status"] = "identity_mismatch"
                        elif info.get("subset"):
                            entry["status"] = "subset_quarantined"
                        elif identity in identities and identities[identity] != digest:
                            entry["status"] = "conflict"
                            for previous in entries[:-1]:
                                if previous.get("sha256") == identities[identity]:
                                    previous["status"] = "conflict"
                        elif digest in hashes:
                            entry["duplicate"] = True
                        identities.setdefault(identity, digest)
                        hashes.add(digest)
            # Mark every member of a conflicting family/face, including later duplicates.
            for entry in entries:
                if "sha256" not in entry:
                    continue
                peers = [
                    other
                    for other in entries
                    if other.get("family") == entry.get("family")
                    and other["face"] == entry["face"]
                    and "sha256" in other
                ]
                if len({other["sha256"] for other in peers}) > 1:
                    for other in peers:
                        other["conflict"] = True
                        if other["status"] == "pending_review":
                            other["status"] = "conflict"
    except (zipfile.BadZipFile, etree.XMLSyntaxError, ValueError, KeyError, RuntimeError):
        report["status"] = "invalid_package"
    return report


def prepare_fonts(pptx: pathlib.Path | bytes, data_dir: pathlib.Path) -> dict[str, Any]:
    """Persist a report before rendering; cache by input bytes and decoder availability."""
    if isinstance(pptx, bytes):
        digest = hashlib.sha256(pptx).hexdigest()
    else:
        with pptx.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    decoder = shutil.which(os.environ.get("PD_EOT2TTF", "eot2ttf")) or ""
    decoder_key = "absent"
    if decoder:
        with pathlib.Path(decoder).open("rb") as stream:
            decoder_key = hashlib.file_digest(stream, "sha256").hexdigest()
    root = data_dir / "embedded-fonts" / digest
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"v{SCHEMA}-{decoder_key}"
    report_path = target / "report.json"
    if report_path.is_file():
        return json.loads(report_path.read_text())  # type: ignore[no-any-return]
    with tempfile.TemporaryDirectory(dir=root, prefix=".prepare-") as tmp:
        work = pathlib.Path(tmp) / "result"
        report = extract(pptx, work, decoder=decoder)
        report.update(pptx_sha256=digest, decoder_sha256=decoder_key)
        (work / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        try:
            work.rename(target)
        except OSError:
            if not report_path.is_file():
                raise
    return json.loads(report_path.read_text())  # type: ignore[no-any-return]
