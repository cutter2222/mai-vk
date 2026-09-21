"""Патч меняет только цикл презентаций; неизвестную сборку не трогаем."""

import gzip
import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "render_patch", Path(__file__).resolve().parents[1] / "scripts/patch_onlyoffice_rendering.py"
)
assert spec and spec.loader
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


def test_patch_is_targeted_and_idempotent(tmp_path, monkeypatch):
    sdk = b"other=40;" + patcher.ANCHOR + b"timer=40;"
    api = b"const ver='" + patcher.VERSION + b"';"
    monkeypatch.setattr(patcher, "SDK_HASH", hashlib.sha256(sdk).hexdigest())
    monkeypatch.setattr(patcher, "API_HASH", hashlib.sha256(api).hexdigest())
    root = tmp_path / "documentserver"
    paths = [root / "sdkjs/slide/sdk-all.js", root / "web-apps/apps/api/documents/api.js"]
    for path, data in zip(paths, [sdk, api], strict=True):
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        path.with_suffix(".js.gz").write_bytes(gzip.compress(data))
    patcher.patch(root)
    patcher.patch(root)
    assert paths[0].read_bytes() == sdk.replace(patcher.ANCHOR, patcher.PATCHED)
    assert paths[1].read_bytes() == api.replace(patcher.VERSION, patcher.CACHE_VERSION)
    for path in paths:
        assert gzip.decompress(path.with_suffix(".js.gz").read_bytes()) == path.read_bytes()
    assert (tmp_path / "rendering-backup-40ms/sdk-all.js").read_bytes() == sdk


def test_unknown_build_fails_before_writing(tmp_path, monkeypatch):
    root = tmp_path / "documentserver"
    sdk = root / "sdkjs/slide/sdk-all.js"
    api = root / "web-apps/apps/api/documents/api.js"
    sdk.parent.mkdir(parents=True)
    api.parent.mkdir(parents=True)
    sdk.write_bytes(patcher.ANCHOR)
    api.write_bytes(b"unsupported API")
    monkeypatch.setattr(patcher, "SDK_HASH", hashlib.sha256(patcher.ANCHOR).hexdigest())
    with pytest.raises(ValueError, match="Неизвестная сборка"):
        patcher.patch(root)
    assert sdk.read_bytes() == patcher.ANCHOR
    assert not (tmp_path / "rendering-backup-40ms").exists()
