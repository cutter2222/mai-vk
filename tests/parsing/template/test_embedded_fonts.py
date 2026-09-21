"""Real sfnt parsing, relationship resolution and quarantine (no installed fonts changed)."""

import io
import json
import os
import pathlib
import shutil
import struct
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from lxml import etree

from presentation_designer.parsing.template import embedded_fonts as ef


def font_bytes(*, otf=False, permissions=0, version="Version 1.0"):
    builder = FontBuilder(1000, isTTF=not otf)
    builder.setupGlyphOrder([".notdef", "A"])
    builder.setupCharacterMap({65: "A"})
    if otf:
        pen = T2CharStringPen(500, None)
        builder.setupCFF(
            "Test-Regular", {}, {name: pen.getCharString() for name in (".notdef", "A")}, {}
        )
    else:
        builder.setupGlyf({name: TTGlyphPen(None).glyph() for name in (".notdef", "A")})
    builder.setupHorizontalMetrics({name: (500, 0) for name in (".notdef", "A")})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": "Test",
            "styleName": "Regular",
            "fullName": "Test Regular",
            "psName": "Test-Regular",
            "version": version,
        }
    )
    builder.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
        fsType=permissions,
    )
    builder.setupPost()
    output = io.BytesIO()
    builder.save(output)
    return output.getvalue()


def package(fonts, *, target="fonts/font.odttf", mode=None, rel_type=None, family="Test"):
    root = etree.Element(f"{{{ef.P}}}presentation", nsmap={"p": ef.P, "r": ef.R})
    lst = etree.SubElement(root, f"{{{ef.P}}}embeddedFontLst")
    rels = etree.Element(f"{{{ef.PKG}}}Relationships")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zf:
        for i, data in enumerate(fonts):
            font = etree.SubElement(lst, f"{{{ef.P}}}embeddedFont")
            etree.SubElement(font, f"{{{ef.P}}}font", typeface=family)
            etree.SubElement(font, f"{{{ef.P}}}regular", {f"{{{ef.R}}}id": f"r{i}"})
            part = target if i == 0 else f"fonts/font{i}.fntdata"
            attrs = {"Id": f"r{i}", "Type": rel_type or f"{ef.R}/font", "Target": part}
            if mode:
                attrs["TargetMode"] = mode
            etree.SubElement(rels, f"{{{ef.PKG}}}Relationship", attrs)
            if not part.startswith(("http", "..", "/")):
                zf.writestr("ppt/" + part, data)
        zf.writestr("ppt/presentation.xml", etree.tostring(root))
        zf.writestr("ppt/_rels/presentation.xml.rels", etree.tostring(rels))
        zf.writestr("[Content_Types].xml", '<Types><Default Extension="odttf"/></Types>')
    return output.getvalue()


def eot(data, flags=0, permissions=0):
    # Version 1: four length-prefixed UTF-16 strings, padding only between them.
    header = bytearray(82)
    struct.pack_into("<IIII", header, 0, 0, len(data), 0x10000, flags)
    struct.pack_into("<HH", header, 32, permissions, 0x504C)
    for text in ("Test", "Regular", "Version 1.0", "Test Regular"):
        value = text.encode("utf-16le")
        header += struct.pack("<H", len(value)) + value + b"\0\0"
    del header[-2:]
    struct.pack_into("<I", header, 0, len(header) + len(data))
    if flags & 0x10000000:
        data = bytes(b ^ 0x50 for b in data)
    return bytes(header) + data


@pytest.mark.parametrize("otf", [False, True])
def test_raw_signature_beats_extension(tmp_path, otf):
    data = font_bytes(otf=otf)
    report = ef.extract(package([data]), tmp_path)
    face = report["fonts"][0]
    assert face["status"] == "pending_review"
    assert face["format"] == ("otf" if otf else "ttf")
    assert face["codepoints"] == [65]
    assert face["coverage"] == "unknown_fullness"
    assert (tmp_path / face["file"]).read_bytes() == data
    assert report["registration"] == "not_registered"


@pytest.mark.parametrize(
    "signature,fmt",
    [(b"wOFF", "woff"), (b"wOF2", "woff2"), (b"ttcf", "collection"), (b"bad!", "odttf_unverified")],
)
def test_unsupported(tmp_path, signature, fmt):
    face = ef.extract(package([signature]), tmp_path)["fonts"][0]
    assert face["status"] == "unsupported_format"
    assert face["source_format"] == fmt
    assert not list(tmp_path.glob("*.ttf"))


@pytest.mark.parametrize(
    "target,mode,rel_type,status",
    [
        ("http://evil/font", "External", None, "external_relationship"),
        ("http://evil/font", None, None, "invalid_relationship"),
        ("../../outside.ttf", None, None, "invalid_relationship"),
        ("%2e%2e/%2e%2e/outside.ttf", None, None, "invalid_relationship"),
        ("fonts\\font.ttf", None, None, "invalid_relationship"),
        ("fonts/a.ttf#fragment", None, None, "invalid_relationship"),
        ("fonts/a.ttf", None, ef.R + "/image", "invalid_relationship"),
    ],
)
def test_unsafe_relationships(tmp_path, target, mode, rel_type, status):
    report = ef.extract(
        package([font_bytes()], target=target, mode=mode, rel_type=rel_type), tmp_path
    )
    assert report["fonts"][0]["status"] == status
    assert not list(tmp_path.glob("*.ttf"))


@pytest.mark.parametrize("permissions", [2, 4, 0x200, 0x202])
def test_permissions(tmp_path, permissions):
    face = ef.extract(package([font_bytes(permissions=permissions)]), tmp_path)["fonts"][0]
    assert face["status"] == "embedding_restricted"
    assert not list(tmp_path.glob("*.ttf"))


def test_identity_and_duplicates_and_conflict(tmp_path):
    data = font_bytes()
    assert (
        ef.extract(package([data], family="Wrong"), tmp_path)["fonts"][0]["status"]
        == "identity_mismatch"
    )
    faces = ef.extract(package([data, data]), tmp_path)["fonts"]
    assert faces[1]["duplicate"] is True
    faces = ef.extract(package([data, font_bytes(version="Version 2.0")]), tmp_path)["fonts"]
    assert [f["status"] for f in faces] == ["conflict", "conflict"]


def test_unused_declaration_and_orphan_not_extracted(tmp_path):
    assert ef.extract(package([]), tmp_path)["fonts"] == []
    assert list(tmp_path.iterdir()) == []


def test_malformed_and_limits(tmp_path, monkeypatch):
    assert ef.extract(b"bad ZIP", tmp_path)["status"] == "invalid_package"
    assert ef.extract(package([b"\0\1\0\0"]), tmp_path)["fonts"][0]["status"] == "invalid_font"
    monkeypatch.setattr(ef.font_worker, "MAX_FONT", 4)
    assert ef.extract(package([b"x" * 5]), tmp_path)["fonts"][0]["status"] == "size_limit"
    monkeypatch.setattr(ef, "MAX_FACES", 0)
    assert ef.extract(package([b"a"]), tmp_path)["status"] == "resource_limit"


def test_eot_missing_decoder_and_header_permissions(tmp_path):
    face = ef.extract(package([eot(font_bytes())]), tmp_path)["fonts"][0]
    assert face["status"] == "decoder_unavailable"
    face = ef.extract(package([eot(font_bytes(), permissions=2)]), tmp_path)["fonts"][0]
    assert face["status"] == "embedding_restricted"


@pytest.fixture
def decoder():
    path = shutil.which(os.environ.get("PD_EOT2TTF", "eot2ttf"))
    if not path:
        pytest.skip("set PD_EOT2TTF to run real libeot tests")
    return path


@pytest.mark.parametrize("flags", [0, 1, 0x10000000, 0x10000001])
def test_real_uncompressed_eot(tmp_path, decoder, flags):
    data = font_bytes()
    face = ef.extract(package([eot(data, flags)]), tmp_path, decoder=decoder)["fonts"][0]
    assert face["status"] == ("subset_quarantined" if flags & 1 else "pending_review")
    assert (tmp_path / face["file"]).read_bytes() == data


@pytest.mark.organizer_data
@pytest.mark.parametrize("xor", [False, True])
def test_real_mtx(tmp_path, organizer_dir, decoder, xor):
    files = sorted(organizer_dir.glob("*Education*.pptx"))
    if not files:
        pytest.skip("organizer template unavailable")
    with zipfile.ZipFile(files[0]) as zf:
        data = bytearray(zf.read("ppt/fonts/Play-regular.fntdata"))
    if xor:
        flags = struct.unpack_from("<I", data, 12)[0]
        struct.pack_into("<I", data, 12, flags | 0x10000000)
        size = struct.unpack_from("<I", data, 4)[0]
        data[-size:] = bytes(b ^ 0x50 for b in data[-size:])
    face = ef.extract(package([bytes(data)], family="Play"), tmp_path, decoder=decoder)["fonts"][0]
    assert face["status"] == "pending_review"
    with TTFont(tmp_path / face["file"]) as font:
        assert len(font.getBestCmap()) == 827
        assert font["name"].getDebugName(1) == "Play"


def test_cache_atomic_and_source_immutable(tmp_path, monkeypatch):
    data = package([font_bytes()])
    source = tmp_path / "source.pptx"
    source.write_bytes(data)
    with ThreadPoolExecutor(max_workers=2) as executor:
        reports = list(executor.map(lambda _: ef.prepare_fonts(source, tmp_path), range(2)))
    assert reports[0] == reports[1]
    monkeypatch.setattr(ef, "extract", lambda *a, **k: pytest.fail("must use cache"))
    assert ef.prepare_fonts(data, tmp_path) == reports[0]
    assert source.read_bytes() == data
    assert len(list((tmp_path / "embedded-fonts").rglob("report.json"))) == 1
    assert not list((tmp_path / "embedded-fonts").rglob(".prepare-*"))


def test_timeout_kills_decoder(tmp_path):
    slow = tmp_path / "slow"
    slow.write_text("#!/bin/sh\nsleep 30\n")
    slow.chmod(0o700)
    report = ef._decode(eot(font_bytes()), tmp_path, str(slow), 0.2)
    assert report["status"] == "decode_timeout"


def test_relative_data_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    report = ef.prepare_fonts(package([font_bytes()]), pathlib.Path("data"))
    assert report["fonts"][0]["status"] == "pending_review"


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "dtd"])
def test_invalid_relationship_metadata(tmp_path, mutation):
    source = package([font_bytes()])
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as original, zipfile.ZipFile(output, "w") as zf:
        for name in original.namelist():
            data = original.read(name)
            if name.endswith(".rels"):
                if mutation == "missing":
                    data = data.replace(b'Id="r0"', b'Id="other"')
                elif mutation == "duplicate":
                    root = etree.fromstring(data)
                    etree.SubElement(root, f"{{{ef.PKG}}}Relationship", dict(root[0].attrib))
                    data = etree.tostring(root)
                else:
                    data = (
                        b'<!DOCTYPE Relationships [<!ENTITY bad SYSTEM "file:///etc/passwd">]>'
                        + data
                    )
            zf.writestr(name, data)
    report = ef.extract(output.getvalue(), tmp_path)
    if mutation == "missing":
        assert report["fonts"][0]["status"] == "invalid_relationship"
    else:
        assert report["status"] == "invalid_package"
    assert not list(tmp_path.glob("*.ttf"))


def test_upload_and_api_diagnostics(client, orchestrator):
    uploaded = client.post("/api/templates", files={"file": ("font.pptx", package([font_bytes()]))})
    assert uploaded.status_code == 202, uploaded.text
    reports = list(orchestrator.settings.paths.data_dir.rglob("report.json"))
    assert len(reports) == 1
    report = client.get(f"/api/templates/{uploaded.json()['template_id']}/fonts")
    assert report.status_code == 200
    assert report.json() == json.loads(reports[0].read_text())
    assert report.json()["fonts"][0]["status"] == "pending_review"
    assert client.get("/api/templates/missing/fonts").status_code == 404
