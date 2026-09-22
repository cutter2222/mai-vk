"""Precision patch is opt-in, build-pinned and cannot overwrite existing files."""

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "precision_patch", Path(__file__).resolve().parents[1] / "scripts/patch_onlyoffice_precision.py"
)
assert spec and spec.loader
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


def fixture_sdk(monkeypatch):
    data = b";".join(before for before, _ in patcher.REPLACEMENTS)
    monkeypatch.setattr(patcher, "SDK_HASH", hashlib.sha256(data).hexdigest())
    return data


def test_all_anchors_change_and_input_is_untouched(tmp_path, monkeypatch):
    data = fixture_sdk(monkeypatch)
    source, output = tmp_path / "source.js", tmp_path / "patched.js"
    source.write_bytes(data)
    digest = patcher.build(source, output)
    assert source.read_bytes() == data
    assert digest == hashlib.sha256(output.read_bytes()).hexdigest()
    for before, after in patcher.REPLACEMENTS:
        assert before not in output.read_bytes()
        assert output.read_bytes().count(after) == 1
    with pytest.raises(FileExistsError):
        patcher.build(source, output)
    with pytest.raises(FileExistsError):
        patcher.build(source, source)
    with pytest.raises(ValueError, match="Unsupported"):
        patcher.transform(output.read_bytes())


def test_unknown_build_writes_nothing(tmp_path):
    source, output = tmp_path / "source.js", tmp_path / "patched.js"
    source.write_bytes(b"unsupported SDK")
    with pytest.raises(ValueError, match="Unsupported"):
        patcher.build(source, output)
    assert not output.exists()


def test_duplicate_anchor_rejected_even_with_matching_hash(monkeypatch):
    data = fixture_sdk(monkeypatch) + patcher.REPLACEMENTS[0][0]
    monkeypatch.setattr(patcher, "SDK_HASH", hashlib.sha256(data).hexdigest())
    with pytest.raises(ValueError, match="ambiguous"):
        patcher.transform(data)


def image_sdk(tmp_path, monkeypatch):
    data = fixture_sdk(monkeypatch)
    patched = patcher.transform(data)
    hashes = {hashlib.sha256(data).hexdigest(): hashlib.sha256(patched).hexdigest()}
    monkeypatch.setattr(patcher, "supported_hashes", lambda: hashes)
    (tmp_path / patcher.SDK_FILE).write_bytes(data)
    (tmp_path / (patcher.SDK_FILE + ".gz")).write_bytes(gzip.compress(data))
    for name in (*patcher.SNAPSHOTS, *patcher.CACHES):
        (tmp_path / name).write_bytes(b"stale binary")
    return data, patched


def test_image_install_and_restart_cache(tmp_path, monkeypatch):
    data, patched = image_sdk(tmp_path, monkeypatch)
    report = patcher.install(tmp_path)
    assert report["source_sha256"] == hashlib.sha256(data).hexdigest()
    assert (tmp_path / patcher.SDK_FILE).read_bytes() == patched
    assert gzip.decompress((tmp_path / (patcher.SDK_FILE + ".gz")).read_bytes()) == patched
    assert json.loads((tmp_path / patcher.MANIFEST).read_text()) == report
    for name in (*patcher.SNAPSHOTS, *patcher.CACHES):
        assert not (tmp_path / name).exists()
    with pytest.raises(FileExistsError):
        patcher.install(tmp_path)
    # Runtime cache can be generated, but must be discarded before the next start.
    for name in patcher.CACHES:
        (tmp_path / name).write_bytes(b"generated code cache")
    assert patcher.verify_install(tmp_path) == report
    assert patcher.verify_install(tmp_path, clear_cache=True) == report
    assert all(not (tmp_path / name).exists() for name in patcher.CACHES)


@pytest.mark.parametrize("name", ["sdk-all.js", "sdk-all.js.gz", *patcher.SNAPSHOTS])
def test_runtime_drift_fails_closed(tmp_path, monkeypatch, name):
    image_sdk(tmp_path, monkeypatch)
    patcher.install(tmp_path)
    (tmp_path / name).write_bytes(gzip.compress(b"wrong") if name.endswith(".gz") else b"wrong")
    (tmp_path / patcher.CACHES[0]).write_bytes(b"retain on failed verification")
    with pytest.raises(ValueError):
        patcher.verify_install(tmp_path, clear_cache=True)
    assert (tmp_path / patcher.CACHES[0]).exists()


def test_forged_manifest_rejected(tmp_path, monkeypatch):
    image_sdk(tmp_path, monkeypatch)
    patcher.install(tmp_path)
    (tmp_path / patcher.MANIFEST).write_text(
        json.dumps({"source_sha256": "unknown", "patched_sha256": "unknown"})
    )
    with pytest.raises(ValueError, match="manifest"):
        patcher.verify_install(tmp_path)


def test_unexpected_output_leaves_image_untouched(tmp_path, monkeypatch):
    image_sdk(tmp_path, monkeypatch)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    monkeypatch.setattr(patcher, "supported_hashes", lambda: {})
    with pytest.raises(ValueError, match="output"):
        patcher.install(tmp_path)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_sdk_symlink_is_not_followed(tmp_path, monkeypatch):
    image_sdk(tmp_path, monkeypatch)
    (tmp_path / patcher.SDK_FILE).rename(tmp_path / "external.js")
    (tmp_path / patcher.SDK_FILE).symlink_to(tmp_path / "external.js")
    before = (tmp_path / "external.js").read_bytes()
    with pytest.raises(ValueError, match="symlink"):
        patcher.install(tmp_path)
    assert (tmp_path / "external.js").read_bytes() == before


def test_gzip_header_regeneration_is_allowed(tmp_path, monkeypatch):
    _, patched = image_sdk(tmp_path, monkeypatch)
    patcher.install(tmp_path)
    (tmp_path / (patcher.SDK_FILE + ".gz")).write_bytes(gzip.compress(patched, mtime=123))
    patcher.verify_install(tmp_path)


@pytest.mark.parametrize("manifest", [[], None, {"source_sha256": 1}, {}])
def test_malformed_manifest_rejected(tmp_path, monkeypatch, manifest):
    image_sdk(tmp_path, monkeypatch)
    patcher.install(tmp_path)
    (tmp_path / patcher.MANIFEST).write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="manifest"):
        patcher.verify_install(tmp_path)
