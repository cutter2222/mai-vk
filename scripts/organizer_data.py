"""Копирует материалы организаторов из local/ в data/organizers/ и пишет manifest.json с хешами.

Исходники лежат в local/ — папке, которая целиком вне Git (шаблоны организаторов и ТЗ в
открытый репозиторий не попадают); data/organizers/ тоже вне Git. Запуск: make organizer-data
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = [
    ROOT / "local" / "Датасет" / "VK Tech шаблон.pptx",
    ROOT / "local" / "Датасет" / "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
    ROOT / "local" / "Датасет" / "Шаблон презентации VK Education.pptx",
    ROOT / "local" / "ЛЦТ2026 Шаблон презентации.pptx",
]
TARGET = ROOT / "data" / "organizers"


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    entries = []
    missing = []
    for src in SOURCES:
        if not src.exists():
            missing.append(str(src.relative_to(ROOT)))
            continue
        dst = TARGET / src.name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dst)
        entries.append(
            {
                "name": src.name,
                "path": str(dst.relative_to(ROOT)),
                "source": str(src.relative_to(ROOT)),
                "size_bytes": dst.stat().st_size,
                "sha256": sha256(dst),
                "role": "lct_pitch_template" if "ЛЦТ" in src.name else "organizer_template",
            }
        )
    (TARGET / "manifest.json").write_text(
        json.dumps({"templates": entries, "content_package": None}, ensure_ascii=False, indent=2)
        + "\n"
    )
    print(f"скопировано {len(entries)} файлов в {TARGET.relative_to(ROOT)}; манифест записан")
    if missing:
        print("не найдены:", *missing, sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
