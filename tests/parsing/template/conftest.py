"""Фикстуры анализа шаблона: собственный синтетический PPTX с группами, иконками, каталогом
иконок, инструкцией, скрытым слайдом и повторяющимся логотипом; путь к шаблонам организаторов."""

from __future__ import annotations

import pathlib

import pytest

from tests.fixtures.rich_template import build_rich_template
from tests.llm.conftest import (  # noqa: F401  # фикстуры клиента моделей
    make_client,
    models,
    settings,
    stub,
)

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "fixtures"
MINI_TEMPLATE = FIXTURES / "pptx" / "mini_template.pptx"


@pytest.fixture(scope="session")
def rich_template(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Шаблон 16:9 из шести слайдов: титул, карточки с иконками в группах, показатели,
    слайд-инструкция, каталог иконок, скрытый слайд; логотип повторяется на всех слайдах."""
    return build_rich_template(tmp_path_factory.mktemp("tpl") / "rich_template.pptx")


@pytest.fixture(scope="session")
def mini_template() -> pathlib.Path:
    return MINI_TEMPLATE
