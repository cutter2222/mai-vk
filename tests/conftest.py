"""Общие фикстуры: примеры контрактов, материалы организаторов, строгий режим приёмки."""

from __future__ import annotations

import json
import os
import pathlib
from collections.abc import Callable

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "contracts" / "examples"
ORGANIZER_DIR = pathlib.Path(os.environ.get("ORGANIZER_DATA_DIR", ROOT / "data" / "organizers"))


@pytest.fixture(scope="session")
def example() -> Callable[[str], dict[str, object]]:
    """Загружает пример по имени: example("slide_plan") или example("job_status.error")."""

    def load(name: str) -> dict[str, object]:
        path = EXAMPLES / f"{name}.example.json"
        return json.loads(path.read_text())  # type: ignore[no-any-return]

    return load


@pytest.fixture(scope="session")
def organizer_dir() -> pathlib.Path:
    return ORGANIZER_DIR


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """organizer_data пропускается без датасета; при REQUIRE_ORGANIZER_DATA=1 падает."""
    manifest = ORGANIZER_DIR / "manifest.json"
    strict = os.environ.get("REQUIRE_ORGANIZER_DATA") == "1"
    if manifest.exists():
        return
    for item in items:
        if "organizer_data" in item.keywords:
            if strict:
                item.add_marker(
                    pytest.mark.xfail(
                        run=False,
                        strict=True,
                        reason="REQUIRE_ORGANIZER_DATA=1, но manifest.json отсутствует",
                    )
                )
            else:
                item.add_marker(
                    pytest.mark.skip(
                        reason="материалы организаторов не скопированы: make organizer-data"
                    )
                )
