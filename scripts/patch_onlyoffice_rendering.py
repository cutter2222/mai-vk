"""Точечный патч цикла презентаций ONLYOFFICE 9.3.1 build 10; fail-closed, без рестарта."""

from __future__ import annotations

import argparse
import gzip
import hashlib
from pathlib import Path

SDK_HASH = "5c1a4d91a185fbb6ff09a68b839d7891a89d918db1f12d2a88d03e9ee823fbd8"
API_HASH = "27f1a26305163f14ed1ce205186ae37bbcd7c3e097671c3e3c640a1ede295c61"
ANCHOR = b"this.O_i=new AscCommon.hpf(40,B);"
PATCHED = b"this.O_i=new AscCommon.hpf(16,B);"
VERSION = b"/9.3.1-118841e3cd9116f13e5f1bdcf35ae068"
CACHE_VERSION = VERSION + b"_pd16v2"


def transform(data: bytes, *, sdk: bool) -> bytes:
    before, after = (ANCHOR, PATCHED) if sdk else (VERSION, CACHE_VERSION)
    original = data.replace(after, before)
    expected = SDK_HASH if sdk else API_HASH
    if hashlib.sha256(original).hexdigest() != expected or original.count(before) != 1:
        raise ValueError("Неизвестная сборка ONLYOFFICE: файлы не изменены")
    return original.replace(before, after)


def patch(root: Path) -> None:
    paths = [root / "sdkjs/slide/sdk-all.js", root / "web-apps/apps/api/documents/api.js"]
    # Проверить оба файла до первой записи. Резервные копии никогда не перезаписываются.
    contents = [transform(path.read_bytes(), sdk=i == 0) for i, path in enumerate(paths)]
    backup = root.parent / "rendering-backup-40ms"
    backup.mkdir(exist_ok=True)
    for path, content in zip(paths, contents, strict=True):
        for source in (path, path.with_suffix(path.suffix + ".gz")):
            target = backup / source.name
            if source.exists() and not target.exists():
                target.write_bytes(source.read_bytes())
        for target, payload in (
            (path, content),
            (path.with_suffix(path.suffix + ".gz"), gzip.compress(content, mtime=0)),
        ):
            temporary = target.with_name(target.name + ".pd16.tmp")
            temporary.write_bytes(payload)
            temporary.chmod(0o644)
            temporary.replace(target)
        print(f"{hashlib.sha256(content).hexdigest()}  {path}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/var/www/onlyoffice/documentserver"))
    patch(parser.parse_args().root)
