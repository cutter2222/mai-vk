"""Conservative topic-only detection, not a factual verification of user material."""

from __future__ import annotations

from typing import Any

CONCEPT_WARNING = "topic_only_concept"
CONCEPT_POLICY = (
    "Режим: концепция по теме, не подтверждённая источниками презентация. "
    "Название, аудитория, цель и список желаемых разделов — задание, не доказательства. "
    "Не утверждай характеристики продукта, даты выпуска, результаты, сравнения и прогнозы "
    "без предоставленных данных, даже без чисел. Предлагай вопросы для исследования, "
    "критерии оценки и явно обозначенные гипотезы; не формулируй их как свершившиеся факты. "
    "Не придумывай источники и цитаты. В key_takeaway и выводах сохраняй статус концепции. "
    "Недостающие материалы перечисли в assumptions. Не заполняй объём пустыми разделителями; "
    "сохраняй явно заданное число слайдов, не выдумывая содержание ради объёма. "
    "Важно: концепция должна раскрывать САМУ ТЕМУ, а не быть лекцией о проверке фактов. "
    "Разработай конкретные авторские предложения и сценарии использования по теме: "
    "какую задачу пользователя предлагаем решить, как мог бы выглядеть сценарий, "
    "какие компромиссы и способы проверки предлагаем. Это проектные идеи, не свойства "
    "реального продукта: формулируй «предлагаем», «идея», «гипотеза», «можно проверить». "
    "Например, для смартфона разные аспекты — съёмка, автономность, взаимодействие, "
    "приватность, доступность; не приписывай конкретной модели существующие функции. "
    "На каждый содержательный слайд дай отдельный предметный тезис и несколько разных "
    "пояснений; не повторяй одну мысль тремя синонимами. Только обложка и один финальный "
    "блок посвящены статусу и следующим шагам. Остальной объём — полезные идеи по теме. "
    "Для N слайдов подготовь N-2 содержательных тезисов плюс начало и вывод; "
    "не трать слайды на оглавление, названия разделов и общую методологию."
)


def topic_only(package: dict[str, Any]) -> bool:
    """Metadata and requested topics alone are not supporting material.

    Notes and extracted content count as supplied material, not verified truth.
    A file upload with no usable content does not disable the concept safeguard.
    """
    if str((package.get("brief") or {}).get("notes") or "").strip():
        return False
    if any(d.get("rows") for d in package.get("datasets", [])):
        return False
    for block in package.get("blocks", []):
        tags = set(block.get("tags") or [])
        if "brief" in tags and "notes" not in tags:
            continue
        if block.get("kind") in ("paragraph", "quote", "bullets"):
            if str(block.get("text") or "").strip() or any(
                str(item).strip() for item in block.get("items", [])
            ):
                return False
        if block.get("kind") == "figure" and block.get("asset_id"):
            return False
    return True


def is_concept(story: dict[str, Any]) -> bool:
    return any(w.get("code") == CONCEPT_WARNING for w in story.get("warnings", []))


def concept_notice(language: str) -> str:
    if language.lower().startswith("ru"):
        return "Концепция по теме: источники не предоставлены; утверждения требуют проверки."
    return "Topic concept: no supporting sources provided; claims require verification."


def concept_title(title: str, language: str) -> str:
    label = "Концепция" if language.lower().startswith("ru") else "Concept"
    return f"{label}: {title}"
