"""Классификация слайдов шаблона: образец содержания, инструкция, каталог ресурсов, пустой, скрытый.

Решение принимается по структуре и тексту слайда, без кода по имени файла: доля картинок
малого размера и их число говорят о каталоге иконок или логотипов; повелительные формулировки
про шрифты, цвета и оформление — об инструкции; наличие слотов с заглушками, таблиц и диаграмм —
об образце. Инструкции и каталоги не становятся паттернами, но их текст и изображения
используются модулями guidelines и assets. Каждому решению приписывается уверенность.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from presentation_designer.parsing.template.geometry import normalize_text
from presentation_designer.parsing.template.package import SlideInfo, TemplatePackage

# Маркеры-заглушки, которыми шаблоны обозначают места для содержания (WORK_PLAN §2, п. 6).
PLACEHOLDER_MARKERS = (
    "заголовок",
    "подзаголовок",
    "текст",
    "описание",
    "пункт",
    "имя фамилия",
    "должность",
    "имя спикера",
    "вставить фото",
    "вставить qr",
    "lorem ipsum",
    "ххх%",
    "xxx%",
    "x%",
    "хх%",
    "хх",
    "показатель",
    "название презентации",
    "название команды",
    "название задачи",
    "заметка",
    "подпись",
    "текстовый блок",
    "тезис",
    "цитата",
    "дата",
    "ссылка",
    "qr-код",
    "qr-code",
    "иллюстрация",
    "заголовок слайда",
    "основной текст",
    "данные показателя",
)

# Слова инструкций по оформлению: если они составляют заметную часть текста, слайд — style_guide.
INSTRUCTION_PATTERNS = (
    r"\bшрифт",
    r"\bкегл",
    r"\bцвет",
    r"\bпалитр",
    r"\bиспользуй",
    r"\bиспользуйте",
    r"\bне используй",
    r"\bудаляем",
    r"\bудали",
    r"\bпример оформлени",
    r"\bоформлени[ея] (схем|таблиц|код|диаграмм|график)",
    r"\bиконки можно",
    r"\bточки используются",
    r"\bперекрытие рядов",
    r"\bбоковой зазор",
    r"\bлинии сетки",
    r"\bправил",
    r"\bрекомендац",
    r"\bне забудь",
    r"\bобязательный блок",
    r"\bэта презентация",
    r"\bпривет, участник",
    r"\bгорячие клавиши",
    r"\bпоможет выбрать",
    r"\bтут ты найд",
    r"\bссылка на шрифт",
    r"https?://",
    r"\bмасштабиру",
    r"\bне менять",
    r"\bдолжн[аоы]? быть",
    r"\bв заголовках уже задан",
    r"\bрамки адаптированы",
    r"\bесли нужно показать",
    r"\bмакет",
)
CATALOG_WORDS = (
    "иконки",
    "иконок",
    "логотипы",
    "логотип",
    "пиктограмм",
    "icons",
    "интерфейсные иконки",
)

_INSTRUCTION_RE = re.compile("|".join(INSTRUCTION_PATTERNS), re.IGNORECASE)


@dataclass
class Classification:
    slide_index: int
    kind: str  # content_sample | style_guide | asset_catalog | empty | hidden | other
    confidence: float
    reasons: list[str] = field(default_factory=list)
    instruction_hits: int = 0
    marker_hits: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "slide_index": self.slide_index,
            "classification": self.kind,
            "confidence": round(self.confidence, 2),
        }


def is_marker(text: str) -> bool:
    """Короткий текст-заглушка вида «Заголовок», «Текст», «Имя Фамилия», «ххх%»."""
    t = normalize_text(text)
    if not t:
        return False
    if len(t) > 60:
        return False
    if t in PLACEHOLDER_MARKERS:
        return True
    return (
        any(t.startswith(m) or t.endswith(m) for m in PLACEHOLDER_MARKERS) and len(t.split()) <= 8
    )


def instruction_hits(text: str) -> int:
    return len(_INSTRUCTION_RE.findall(text or ""))


def classify_slide(slide: SlideInfo, pkg: TemplatePackage) -> Classification:
    if slide.hidden:
        return Classification(slide.index, "hidden", 1.0, ["slide@show=0"])
    shapes = [s for s in slide.shapes if s.kind != "group"]
    pictures = [s for s in shapes if s.kind == "picture"]
    texts = [s for s in shapes if s.has_text_frame and s.text]
    structured = [s for s in shapes if s.kind in ("table", "chart", "smartart")]
    layout = pkg.layout(slide.layout_id)
    layout_placeholders = [
        p
        for p in (layout.placeholders if layout else [])
        if p.placeholder_type not in ("sldNum", "dt", "ftr")
    ]
    all_text = slide.all_text
    hits = instruction_hits(all_text)
    markers = sum(1 for s in texts if is_marker(s.text))
    words = len(all_text.split())
    reasons: list[str] = []

    # Пустой слайд: ни фигур с содержанием, ни плейсхолдеров макета.
    content_shapes = [
        s for s in shapes if s.text or s.kind in ("picture", "table", "chart", "smartart")
    ]
    if (
        not content_shapes
        and not layout_placeholders
        and not [s for s in shapes if s.is_placeholder]
    ):
        return Classification(slide.index, "empty", 0.9, ["нет объектов с содержанием"])

    # Каталог ресурсов: много мелких картинок или слова «иконки/логотипы» при десятке картинок.
    # Карточки «иконка + заголовок + текст» тоже дают много мелких картинок, но у них есть
    # маркеры-заглушки и текста не меньше, чем картинок; у каталога подписей мало.
    if pictures and markers < 2:
        areas = sorted(p.area for p in pictures)
        median_area = statistics.median(areas)
        small = sum(1 for a in areas if a < 0.012)
        catalog_words = any(w in normalize_text(all_text) for w in CATALOG_WORDS)
        sparse_text = len(texts) <= 3 or len(pictures) / max(len(texts), 1) >= 3
        if len(pictures) >= 12 and small / len(pictures) >= 0.7 and sparse_text:
            reasons.append(
                f"{len(pictures)} картинок, {small} мелких (медиана площади {median_area:.4f}), "
                f"подписей {len(texts)}"
            )
            return Classification(slide.index, "asset_catalog", 0.9, reasons, hits, markers)
        if len(pictures) >= 6 and catalog_words and small / len(pictures) >= 0.5:
            reasons.append(f"{len(pictures)} картинок и слова каталога в тексте")
            return Classification(slide.index, "asset_catalog", 0.8, reasons, hits, markers)

    # Инструкция: заметная доля инструктивных формулировок при отсутствии структурных объектов
    # и слотов-заглушек, либо явные ссылки на шрифты/цвета/правила.
    instruction_density = hits / max(words / 25.0, 1.0)  # попаданий на ~25 слов
    if (
        hits >= 2
        and not structured
        and markers <= 1
        and (instruction_density >= 1.0 or words >= 40)
    ):
        reasons.append(f"{hits} инструктивных формулировок при {words} словах, маркеров {markers}")
        return Classification(
            slide.index, "style_guide", min(0.95, 0.6 + 0.1 * hits), reasons, hits, markers
        )
    if hits >= 4 and markers <= 2:
        reasons.append(f"{hits} инструктивных формулировок")
        return Classification(slide.index, "style_guide", 0.75, reasons, hits, markers)

    # Образец содержания: слоты-заглушки, структурные объекты, плейсхолдеры или текст с картинками.
    confidence = 0.6
    if markers >= 2:
        confidence += 0.2
        reasons.append(f"маркеров-заглушек {markers}")
    if structured:
        confidence += 0.15
        reasons.append("есть таблица/диаграмма")
    if [s for s in shapes if s.is_placeholder] or layout_placeholders:
        confidence += 0.1
        reasons.append("плейсхолдеры")
    if hits:
        confidence -= 0.1 * min(hits, 3)
        reasons.append(f"есть {hits} инструктивных фраз — правила извлечены отдельно")
    if not texts and not pictures and not structured:
        # Только плейсхолдеры макета без текста: образец «по макету».
        reasons.append("содержание задаёт макет")
        return Classification(slide.index, "content_sample", 0.55, reasons, hits, markers)
    return Classification(
        slide.index, "content_sample", max(0.4, min(confidence, 0.95)), reasons, hits, markers
    )


def classify_all(pkg: TemplatePackage) -> list[Classification]:
    return [classify_slide(slide, pkg) for slide in pkg.slides]


def placeholder_markers(pkg: TemplatePackage, classes: list[Classification]) -> list[str]:
    """Словарь заглушек: короткие тексты образцов, совпадающие со известными маркерами
    или повторяющиеся не меньше чем на трёх слайдах."""
    counts: dict[str, int] = {}
    originals: dict[str, str] = {}
    sample_indexes = {c.slide_index for c in classes if c.kind == "content_sample"}
    for slide in pkg.slides:
        if slide.index not in sample_indexes:
            continue
        seen: set[str] = set()
        for shape in slide.shapes:
            if not shape.text:
                continue
            for line in shape.text.splitlines():
                key = normalize_text(line)
                if not key or len(key) > 40 or key in seen or instruction_hits(key):
                    continue
                seen.add(key)
                counts[key] = counts.get(key, 0) + 1
                originals.setdefault(key, line.strip())
    out = {originals[k] for k, n in counts.items() if is_marker(k) or n >= 3}
    return sorted(out, key=lambda s: (-counts[normalize_text(s)], s))[:60]
