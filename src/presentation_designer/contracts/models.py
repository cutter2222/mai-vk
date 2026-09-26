# Сгенерировано scripts/gen_contracts.py из contracts/schemas. Не редактировать вручную.

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, RootModel
from typing import Any, Literal


class Font(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    requested: str | None = None
    actual: str | None = None
    file: str | None = None


class Check(BaseModel):
    check_id: str = Field(..., pattern="^(layout|template|density|integrity|content)\\.[a-z0-9_]+$")
    name: str
    category: Literal["layout", "template", "density", "integrity", "content"]
    kind: Literal["deterministic", "contextual"]
    version: str
    scope: Literal["slide", "deck"]
    origin: Literal["appendix1", "own"] | None = None
    implemented: bool
    threshold: dict[str, Any] | None = None
    applicability: str | None = None
    """
    правило применимости: роли паттернов или условия, при которых проверка не применяется
    """
    inputs: (
        list[
            Literal[
                "xml",
                "composed_deck",
                "render",
                "font_metrics",
                "template_profile",
                "story_plan",
                "content_package",
                "neighbor_slides",
                "whole_deck_text",
            ]
        ]
        | None
    ) = None
    """
    входы проверки; для контекстных показывает, что модель получила помимо картинки
    """


class Summary(BaseModel):
    issues_total: int
    by_severity: dict[str, int]
    by_category: dict[str, int] | None = None
    by_kind: dict[str, int] | None = None
    slides_with_issues: int | None = None
    score: float | None = Field(None, ge=0.0, le=100.0)


class Metrics(BaseModel):
    duration_ms: int | None = None
    llm_calls: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class Coverage(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    complete: bool
    """
    все обязательные проверки выполнены
    """
    checked: int | None = None
    not_checked: int | None = None
    not_applicable: int | None = None
    missing_inputs: list[str] | None = None
    """
    например: vlm_unavailable, render_failed
    """


class Brief(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    purpose: Literal["feature", "product", "project", "initiative", "report", "other"] | None = None
    title: str | None = None
    audience: str | None = None
    goal: str | None = None
    language: str | None = None
    tone: str | None = None
    must_include: list[str] | None = None
    avoid: list[str] | None = None


class SlideCount(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    exact: int | None = Field(None, ge=1, le=60)
    min: int | None = Field(None, ge=1, le=60)
    max: int | None = Field(None, ge=1, le=60)


class Id(RootModel[str]):
    root: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """


class Bbox(BaseModel):
    """
    Прямоугольник в долях ширины и высоты слайда; начало координат в левом верхнем углу. Значения вне 0..1 допустимы: так описываются элементы, вышедшие за слайд.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    x: float = Field(..., ge=-1.0, le=2.0)
    y: float = Field(..., ge=-1.0, le=2.0)
    width: float = Field(..., ge=0.0, le=3.0)
    height: float = Field(..., ge=0.0, le=3.0)


class SlideSize(BaseModel):
    """
    Размер слайда в EMU. В датасете встречаются 12192000×6858000 и 9144000×5143500, поэтому кегли сравниваются только внутри одного шаблона.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    width_emu: int
    height_emu: int
    aspect_ratio: float


class Color(RootModel[str]):
    root: str = Field(..., pattern="^#[0-9A-Fa-f]{6}$")
    """
    Цвет в формате #RRGGBB
    """


class StyleSource(BaseModel):
    """
    Откуда унаследовано вычисленное свойство стиля
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    level: Literal[
        "theme",
        "master",
        "layout",
        "placeholder",
        "shape",
        "paragraph",
        "run",
        "table_style",
        "chart_style",
        "default",
    ]
    part: str | None = None
    """
    часть пакета, например ppt/slideMasters/slideMaster1.xml
    """
    theme_ref: str | None = None
    """
    ссылка на цвет или шрифт темы, например accent1 или +mj-lt
    """
    modifiers: list[str] | None = None
    """
    модификаторы цвета темы: lumMod, lumOff, alpha и т. п.
    """


class FontSpec(BaseModel):
    """
    Шрифт текстовой области. size_pt всегда в пунктах и всегда вместе с размером слайда в профиле.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    family: str | None = None
    size_pt: float | None = Field(None, ge=1.0)
    bold: bool | None = None
    italic: bool | None = None
    color: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    """
    Цвет в формате #RRGGBB
    """
    line_spacing: float | None = None
    """
    множитель межстрочного интервала
    """
    all_caps: bool | None = None


class Bullet(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["none", "char", "number", "picture"] | None = None
    char: str | None = None


class ComputedTextStyle(BaseModel):
    """
    Вычисленный стиль текста после разрешения наследования, с источником каждого свойства
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    font: FontSpec | None = None
    font_source: StyleSource | None = None
    color_source: StyleSource | None = None
    size_source: StyleSource | None = None
    space_before_pt: float | None = None
    space_after_pt: float | None = None
    indent_emu: int | None = None
    bullet: Bullet | None = None


class Insets(BaseModel):
    """
    внутренние поля рамки в долях ширины и высоты слайда
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    left: float | None = None
    top: float | None = None
    right: float | None = None
    bottom: float | None = None


class ParagraphParams(BaseModel):
    """
    Параметры абзаца, нужные для измерения вместимости
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    line_spacing: float | None = None
    space_before_pt: float | None = None
    space_after_pt: float | None = None
    indent_emu: int | None = None
    bullet_indent_emu: int | None = None
    insets: Insets | None = None
    """
    внутренние поля рамки в долях ширины и высоты слайда
    """
    autofit: Literal["none", "shrink", "resize_shape"] | None = None
    word_wrap: bool | None = None


class Warning(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    code: str
    message: str
    slide_index: int | None = Field(None, ge=0)


class VersionRef(BaseModel):
    """
    Ссылка на версионируемый компонент: скилл, промпт, анализатор, рендерер
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    name: str
    version: str


class ModelRef(BaseModel):
    """
    Модель, использованная на этапе. Заполняется из конфигурации моделей; hf_url обязателен для MODELS.md.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    role: Literal["llm", "vlm", "text_to_image", "embedding"]
    name: str
    provider: str | None = None
    hf_url: str | None = None
    params_b: float | None = None
    active_params_b: float | None = None
    """
    активные параметры для MoE
    """
    license: str | None = None
    reasoning_mode: str | None = None


class Progress(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    percent: float | None = Field(None, ge=0.0, le=100.0)
    message: str | None = None


class ExecutionMode(BaseModel):
    """
    Какие слои работали по-настоящему, а какие заглушками. Заглушечный результат не выдаётся за генерацию.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    mode: Literal["real", "mixed", "stub"]
    layers: dict[str, Literal["real", "stub", "replay", "skipped"]]
    """
    ключи: parsing.template, parsing.content, brief, generation.story, generation.plan, generation.edit, layout, export, audit.deterministic, audit.contextual
    """


class AssetSource(BaseModel):
    """
    Источник картинки для ручной правки: ресурс шаблона (asset_id из TemplateProfile.assets), ресурс контент-пакета (asset_id из ContentPackage.assets) или загруженный файл проекта (file_id; sha256 дописывает конвейер, name — для подписи в интерфейсе)
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["template", "package", "file"]
    asset_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    file_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    sha256: str | None = None
    name: str | None = None


class Target(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    object_id: str
    source_object_id: str | None = None
    slot_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """


class Font1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    family: str | None = None
    size_pt: float | None = Field(None, ge=6.0, le=120.0)
    bold: bool | None = None
    italic: bool | None = None
    color: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    """
    Цвет в формате #RRGGBB
    """


class Style(BaseModel):
    """
    оформление всех фрагментов объекта; передаются только изменяемые свойства; у add_text — оформление создаваемой надписи
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    font: Font1 | None = None
    align: Literal["left", "center", "right", "justify"] | None = None


class Geometry(BaseModel):
    """
    новое положение и размер в долях слайда (как bbox ComposedDeck, с учётом групп); у add_text — рамка создаваемой надписи
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    bbox: Bbox


class Picture(BaseModel):
    """
    замена картинки или иконки объекта p:pic; color — перекраска монохромной иконки шаблона
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    source: AssetSource
    fit: Literal["cover", "contain"] | None = None
    color: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    """
    Цвет в формате #RRGGBB
    """


class Background(BaseModel):
    """
    фон слайда: сплошной цвет, картинка или наследование от макета
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["solid", "image", "inherited"]
    color: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    """
    Цвет в формате #RRGGBB
    """
    source: AssetSource | None = None
    fit: Literal["cover", "contain"] | None = None


class Override(BaseModel):
    """
    Ручная правка одного объекта слайда или фона слайда из визуального редактора. Применяется композером после заполнения слота и чистки слайда, поэтому ревизия воспроизводится из плана. target.object_id — p:cNvPr@id объекта в ComposedDeck базовой ревизии (у объектов клона образца совпадает с образцом, у новых объектов детерминирован); source_object_id и slot_id — охрана адреса: при несовпадении правка отбрасывается с предупреждением, а не применяется к чужому объекту. Фон (op background) относится к слайду целиком, target не нужен. Операция delete убирает объект со слайда; add_text создаёт свою надпись, её target.object_id придуман редактором (объекта с таким идентификатором в базовой ревизии нет), а идентификатор готовой фигуры выводится из него детерминированно, поэтому пересборка ревизии повторяема.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal["text", "style", "geometry", "picture", "background", "delete", "add_text"]
    target: Target | None = None
    text: str | None = None
    """
    новый текст объекта (op text) или текст создаваемой надписи (op add_text): строки через \\n; ссылки {fact:<id>} подставляются как в плане
    """
    style: Style | None = None
    """
    оформление всех фрагментов объекта; передаются только изменяемые свойства; у add_text — оформление создаваемой надписи
    """
    geometry: Geometry | None = None
    """
    новое положение и размер в долях слайда (как bbox ComposedDeck, с учётом групп); у add_text — рамка создаваемой надписи
    """
    picture: Picture | None = None
    """
    замена картинки или иконки объекта p:pic; color — перекраска монохромной иконки шаблона
    """
    background: Background | None = None
    """
    фон слайда: сплошной цвет, картинка или наследование от макета
    """


class Asset(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    asset_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    media_path: str
    sha256: str
    content_type: str | None = None
    origin: Literal["template", "content", "generated", "icon_library"] | None = None
    shared_with_template: bool | None = None
    """
    неизменяемый ресурс, разделяемый с исходным пакетом
    """
    artifact: str | None = None
    """
    имя артефакта ревизии с байтами ресурса (<variant>/r<N>/media/<file>), если медиа выложено рядом с ревизией
    """


class FallbackElement(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    slide_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    object_id: str
    reason: str
    fallback: Literal["raster_from_render", "omitted"] | None = None


class File(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    weight: Literal[400, 700]
    artifact: str
    format: Literal["truetype", "opentype"] | None = None


class Font2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    family: str
    available_in_renderer: bool | None = None
    fallback: str | None = None
    embedded: bool | None = None
    files: list[File] | None = None
    """
    файлы гарнитуры, приложенные к ревизии: по ним страница и холст редактора показывают текст тем же шрифтом, каким колода мерилась и рисовалась
    """


class Stats(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    slides: int | None = None
    objects: int | None = None
    text_objects: int | None = None
    pictures: int | None = None
    tables: int | None = None
    charts: int | None = None
    diagrams: int | None = None
    removed_objects: int | None = None
    layouts_kept: int | None = None
    layouts_removed: int | None = None
    file_size_bytes: int | None = None


class SlideCount1(BaseModel):
    min: int | None = Field(None, ge=1)
    max: int | None = Field(None, ge=1)


class Brief1(BaseModel):
    purpose: Literal["feature", "product", "project", "initiative", "report", "other"]
    title: str
    audience: str | None = None
    goal: str | None = None
    """
    чего должна добиться презентация: одобрение, информирование, продажа
    """
    language: str
    tone: str | None = None
    slide_count: SlideCount1 | None = None
    must_include: list[str] | None = None
    avoid: list[str] | None = None
    notes: str | None = None


class Units(BaseModel):
    """
    сколько единиц содержания разобрано: страниц, листов, слайдов, таблиц, изображений
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    pages: int | None = Field(None, ge=0)
    sheets: int | None = Field(None, ge=0)
    slides: int | None = Field(None, ge=0)
    tables: int | None = Field(None, ge=0)
    images: int | None = Field(None, ge=0)
    chars: int | None = Field(None, ge=0)


class Source(BaseModel):
    source_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    kind: Literal[
        "text",
        "markdown",
        "docx",
        "pdf",
        "xlsx",
        "csv",
        "json",
        "image",
        "pptx",
        "url",
        "user_input",
    ]
    name: str
    sha256: str | None = None
    size_bytes: int | None = None
    extracted: bool | None = None
    warnings: list[Warning] | None = None
    file_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    файл проекта, из которого извлечён источник; отсутствует у брифа и внешних ссылок
    """
    parser: VersionRef | None = None
    """
    парсер, разобравший файл: имя и версия входят в ключ кэша импорта
    """
    units: Units | None = None
    """
    сколько единиц содержания разобрано: страниц, листов, слайдов, таблиц, изображений
    """


class SourceLocation(BaseModel):
    """
    Место блока в источнике
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    page: int | None = Field(None, ge=1)
    sheet: str | None = None
    slide: int | None = Field(None, ge=1)
    """
    номер слайда материала-презентации
    """
    cell_range: str | None = None
    """
    например A1:C4
    """
    char_offset: int | None = Field(None, ge=0)
    notes: bool | None = None
    """
    текст из заметок докладчика
    """


class Block(BaseModel):
    block_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    source_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    order: int
    kind: Literal["heading", "paragraph", "bullets", "table", "figure", "quote", "kpi", "code"]
    level: int | None = None
    """
    уровень заголовка
    """
    text: str | None = None
    items: list[str] | None = None
    dataset_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    asset_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    importance: Literal["must", "should", "could"] | None = None
    tags: list[str] | None = None
    source_location: SourceLocation | None = None
    """
    Место блока в источнике
    """
    caption: str | None = None
    """
    подпись таблицы или рисунка из источника
    """


class Context(BaseModel):
    """
    Что именно измеряет факт. «Рост выручки на 25 %» и «рост прибыли на 25 %» различаются контекстом, а не значением.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    metric: str | None = None
    """
    показатель: выручка, DAU, доля рынка
    """
    period: str | None = None
    """
    период: 2025 год, 3 квартал, май
    """
    subject: str | None = None
    """
    субъект: компания, продукт, сегмент
    """
    unit: str | None = None
    comparison: str | None = None
    """
    с чем сравнение: год к году, план
    """


class SourceLocation1(BaseModel):
    """
    Точное место в источнике
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    fragment: str | None = None
    """
    исходный фрагмент текста вокруг факта
    """
    page: int | None = None
    sheet: str | None = None
    cell: str | None = None
    """
    например B12
    """
    char_offset: int | None = None


class Derived(BaseModel):
    """
    Производный показатель, вычисленный кодом из других фактов
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    formula: str
    """
    например (f2 - f1) / f1 * 100
    """
    inputs: list[Id]


class Uncertainty(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    level: Literal["confirmed", "inferred", "assumed", "missing"] | None = None
    note: str | None = None
    extracted_by: Literal["regex", "parser", "model", "user"] | None = None
    confidence: float | None = Field(None, ge=0.0, le=1.0)


class Fact(BaseModel):
    fact_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    source_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    block_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    raw: str
    """
    как написано в источнике: «12,5 млн ₽»
    """
    kind: Literal["number", "percent", "money", "date", "range", "ratio", "name", "other"]
    value: float | str | None = None
    unit: str | None = None
    label: str | None = None
    """
    что измеряет факт: «выручка за 2025 год»
    """
    must_keep: bool | None = True
    context: Context | None = None
    """
    Что именно измеряет факт. «Рост выручки на 25 %» и «рост прибыли на 25 %» различаются контекстом, а не значением.
    """
    source_location: SourceLocation1 | None = None
    """
    Точное место в источнике
    """
    derived: Derived | None = None
    """
    Производный показатель, вычисленный кодом из других фактов
    """
    uncertainty: Uncertainty | None = None


class Asset1(BaseModel):
    asset_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    kind: Literal["image", "logo", "screenshot", "chart_image", "diagram_image", "photo"]
    path: str
    """
    путь в рабочем каталоге задания
    """
    width_px: int | None = None
    height_px: int | None = None
    caption: str | None = None
    source_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    sha256: str | None = None
    mime: str | None = None


class SourceChart(BaseModel):
    """
    Validated raster chart transcription; original asset remains available
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    type: Literal["column", "bar", "line"]
    asset_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    axis_minimum: float | None = None
    axis_maximum: float | None = None


class Column(BaseModel):
    name: str
    type: Literal["string", "number", "date", "percent", "money"]
    unit: str | None = None


class SourceLocation2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    page: int | None = Field(None, ge=1)
    sheet: str | None = None
    slide: int | None = Field(None, ge=1)
    cell_range: str | None = None


class Dataset(BaseModel):
    source_chart: SourceChart | None = None
    """
    Validated raster chart transcription; original asset remains available
    """
    dataset_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    title: str | None = None
    columns: list[Column]
    rows: list[list[str | float | None]]
    source_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    block_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    source_location: SourceLocation2 | None = None
    total_rows: int | None = Field(None, ge=0)
    """
    строк в источнике до усечения
    """
    truncated: bool | None = None
    """
    rows усечены до предела импорта; total_rows хранит полное число
    """


class MissingDatum(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    what: str
    why_needed: str | None = None
    thesis_hint: str | None = None


class Cache(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    files_hit: int | None = Field(None, ge=0)
    files_missed: int | None = Field(None, ge=0)


class ImportMeta(BaseModel):
    """
    Как был собран пакет: версии импортёра и парсеров, ключ кэша, попадания в кэш разбора файлов, вызовы модели для уточнения контекста фактов
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    importer: VersionRef
    parsers: dict[str, str] | None = None
    """
    версия парсера по формату: docx, xlsx, csv, pdf, markdown, text, pptx, image
    """
    import_key: str
    """
    sha256 от sha256 файлов в их порядке, параметров разбора и версий парсеров; бриф в ключ не входит — его смена не перечитывает файлы
    """
    cache: Cache | None = None
    skills: list[VersionRef] | None = None
    prompts: list[VersionRef] | None = None
    models: list[ModelRef] | None = None
    model_calls: int | None = Field(None, ge=0)
    """
    запросов к модели для уточнения контекста фактов
    """
    duration_ms: int | None = Field(None, ge=0)
    created_at: AwareDatetime | None = None


class ContentPackage(BaseModel):
    """
    Результат слоя импорта содержания. Два входа: контент-пакет (файлы) и краткий бриф с назначением. Факты и наборы данных извлекаются детерминированно до вызова модели. Версия 1.3 (этап 6): import_meta с версиями парсеров, ключом кэша и вызовами модели; у источников parser и число единиц (страниц, листов, слайдов); у блоков source_location и caption; у ресурсов sha256 и mime; у наборов данных source_location, total_rows и truncated. Версия 1.2: источник ссылается на файл проекта (file_id), вид pptx для материалов-презентаций, неполный бриф дополняется умолчаниями с предупреждением brief_incomplete. Версия 1.1: контекст факта (показатель, период, субъект, единица, исходный фрагмент или ячейка), производные показатели с формулой, отметка неопределённости.
    """

    schema_version: Literal["1.3"]
    package_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    mode: Literal["package", "brief", "mixed"]
    """
    package: содержание дано; brief: структуру и текст генерирует сервис; mixed: бриф плюс материалы
    """
    created_at: AwareDatetime | None = None
    brief: Brief1
    sources: list[Source]
    blocks: list[Block]
    """
    Содержание в порядке исходников. Планировщик ссылается на block_id, а не копирует текст без ссылки.
    """
    facts: list[Fact]
    """
    Реестр фактов. В тексте планов факты подставляются через {fact:<fact_id>}; модель не переписывает значения.
    """
    assets: list[Asset1]
    datasets: list[Dataset]
    """
    Табличные данные для таблиц и графиков
    """
    warnings: list[Warning] | None = None
    missing_data: list[MissingDatum] | None = None
    """
    Данные, которых не хватает для брифа: не выдумываются, а помечаются
    """
    import_meta: ImportMeta | None = None
    """
    Как был собран пакет: версии импортёра и парсеров, ключ кэша, попадания в кэш разбора файлов, вызовы модели для уточнения контекста фактов
    """


class SlideCount2(BaseModel):
    """
    точное число или диапазон; при обоих заданных exact имеет приоритет; min <= max проверяется валидатором
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    exact: int | None = Field(None, ge=1, le=60)
    min: int | None = Field(None, ge=1, le=60)
    max: int | None = Field(None, ge=1, le=60)


class Settings(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    design_mode: Literal["template_only", "mixed", "all_new"] | None = "mixed"
    """
    Композиции: только шаблон, шаблон и новые, либо только новые в стиле шаблона. Независимо от плотности содержания.
    """
    slide_count: SlideCount2 | None = None
    """
    точное число или диапазон; при обоих заданных exact имеет приоритет; min <= max проверяется валидатором
    """
    language: str | None = "ru"
    variants: list[Literal["compact", "balanced", "detailed", "original"]] | None = Field(
        ["compact", "balanced", "detailed"], max_length=3, min_length=1
    )
    """
    варианты вёрстки; original — исходная презентация как есть, только один в списке (проверяется валидатором)
    """
    generate_images: bool | None = False
    """
    генерация новых изображений моделью; доступно только при включённой возможности сервиса
    """
    run_contextual_audit: bool | None = True
    """
    отключение даёт неполный аудит и статус needs_review
    """
    seed: int | None = None
    """
    передаётся провайдеру, побитовая воспроизводимость не гарантируется
    """
    force_regenerate: bool | None = False
    """
    явная перегенерация: кэш ответов модели не используется
    """


class GenerationRequest(BaseModel):
    """
    Тело POST /api/generations и вход CLI-команды generate. Версия 1.2: вариант original — загруженная презентация как готовый результат (шаблон и материал — один и тот же файл; план строится из профиля без модели, слайды и тексты сохраняются как есть; сочетается только сам с собой). Приоритет: явные настройки запроса → бриф ContentPackage → умолчания config/app.yaml. Пути файлов от клиента не принимаются: только идентификаторы.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    template_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    package_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    settings: Settings | None = None
    schema_version: Literal["1.2"]
    idempotency_key: str | None = Field(None, max_length=128)
    """
    повтор запроса с тем же ключом возвращает то же задание
    """


class Thumbnail(BaseModel):
    slide_index: int
    name: str
    width_px: int | None = None
    height_px: int | None = None


class Artifacts(BaseModel):
    pptx: str | None = None
    pdf: str | None = None
    html: str | None = None
    thumbnails: list[Thumbnail] | None = None


class Revision1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    revision: int = Field(..., ge=1)
    """
    Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
    """
    created_at: AwareDatetime
    repair_job_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    changed_slide_ids: list[Id] | None = None
    pptx_hash: str | None = None
    artifacts_prefix: str | None = None


class Audit(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    status: Literal["pending", "running", "complete", "partial", "failed", "skipped"] | None = None
    coverage_complete: bool | None = None
    issues_total: int | None = None
    blocking: int | None = None
    report_artifact: str | None = None


class ArtifactsManifest(BaseModel):
    content_type: str
    size_bytes: int
    sha256: str | None = None


class LlmCall(BaseModel):
    stage: str
    variant_id: str | None = None
    slide_ids: list[str] | None = None
    attempt: int
    model: str
    prompt: VersionRef | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    cache_hit: bool | None = None
    ok: bool | None = None
    quota_wait_ms: int | None = None
    reasoning_tokens: int | None = None
    error_code: str | None = None


class Totals(BaseModel):
    duration_ms: int | None = None
    llm_calls: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    peak_memory_mb: int | None = None


class CostEstimate(BaseModel):
    known: bool
    currency: str | None = None
    amount: float | None = None


class Cache1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    llm_hits: int | None = None
    llm_misses: int | None = None
    profile_hit: bool | None = None
    story_hit: bool | None = None
    render_hits: int | None = None


class Timeline(BaseModel):
    """
    ключевые моменты от принятия задания
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    accepted_at: AwareDatetime | None = None
    first_file_ready_ms: int | None = None
    first_variant_audited_ms: int | None = None
    all_variants_ready_ms: int | None = None
    all_variants_audited_ms: int | None = None


class Font3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    family: str | None = None
    file: str | None = None
    sha256: str | None = None


class Versions(BaseModel):
    """
    Всё, что нужно для воспроизведения: версии приложения, скиллов, промптов, моделей и рендерера
    """

    app: str
    skills: list[VersionRef]
    prompts: list[VersionRef] | None = None
    models: list[ModelRef]
    renderer: VersionRef | None = None
    analyzer: VersionRef | None = None
    fonts: list[Font3] | None = None
    contracts: str | None = None
    """
    версия контрактов, например 1.1
    """
    image_digest: str | None = None
    """
    digest образа воркера
    """


class Repair(BaseModel):
    repair_job_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    variant_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    issue_ids: list[str]
    result: Literal["applied", "partially_applied", "failed", "skipped"]
    message: str | None = None
    base_revision: int | None = Field(None, ge=1)
    """
    Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
    """
    new_revision: int | None = Field(None, ge=1)
    """
    Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
    """
    changed_slide_ids: list[Id] | None = None


class Edit(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    edit_job_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    variant_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    base_revision: int = Field(..., ge=1)
    """
    Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
    """
    slide_index: int = Field(..., ge=0)
    """
    индекс слайда в ревизии, с нуля, как в миниатюрах и отчёте аудита
    """
    slide_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    instruction: str
    result: Literal["applied", "unchanged", "failed"]
    change_note: str | None = None
    """
    что изменено (applied) или почему слайд оставлен как есть (unchanged)
    """
    message: str | None = None
    """
    сообщение об ошибке для failed
    """
    new_revision: int | None = Field(None, ge=1)
    """
    Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
    """
    changed_slide_ids: list[Id] | None = None
    origin: Literal["chat", "editor"] | None = None
    """
    chat — инструкция, выполненная моделью (по умолчанию); editor — ручные правки объектов без модели
    """
    summary: str | None = None
    """
    сводка ручных правок для карточки в чате: какие слайды и что изменено
    """


class Result1(BaseModel):
    """
    ссылка на результат своего вида; заполняется по мере готовности
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    template_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    package_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    generation_result_url: str | None = None
    template_profile_url: str | None = None
    content_package_url: str | None = None
    revision: int | None = Field(None, ge=1)
    """
    Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
    """
    unchanged: bool | None = None
    """
    для правки слайда: просьба отклонена, ревизия не создана
    """
    change_note: str | None = None
    """
    для правки слайда: что изменено или почему слайд оставлен как есть
    """
    changed_slide_ids: list[Id] | None = None
    """
    для ручных правок (slide_patch): слайды, изменённые в новой ревизии
    """


class Check1(BaseModel):
    """
    Результат проверки при загрузке: ZIP и тип содержимого для pptx/docx/xlsx, свой парсер для pdf, текста и изображений; неподдерживаемые типы принимаются без проверки
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    status: Literal["ok", "rejected", "skipped"]
    format: (
        Literal["pptx", "docx", "xlsx", "csv", "pdf", "markdown", "text", "image", "other"] | None
    ) = None
    message: str | None = None


class ProjectFile(BaseModel):
    """
    Файл проекта: шаблон, материал или что-то ещё, что пользователь положил в чат. Байты хранятся на сервере один раз по sha256, запись проекта ссылается на них; вид файла меняется пользователем (PPTX может быть и шаблоном, и материалом). Версия 1.2.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: Literal["1.2"]
    file_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    name: str = Field(..., max_length=255, min_length=1)
    size_bytes: int = Field(..., ge=0)
    sha256: str = Field(..., pattern="^[0-9a-f]{64}$")
    mime: str
    kind: Literal["template", "material", "other"]
    """
    template: шаблон оформления; material: содержание для импорта; other: хранится в проекте без импорта
    """
    added_at: AwareDatetime
    check: Check1
    """
    Результат проверки при загрузке: ZIP и тип содержимого для pptx/docx/xlsx, свой парсер для pdf, текста и изображений; неподдерживаемые типы принимаются без проверки
    """
    template_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    для шаблона: идентификатор после загрузки в библиотеку
    """
    package_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    для материала: пакет, в который он импортирован последним
    """


class Prompt(BaseModel):
    id: str
    path: str
    """
    относительный путь к файлу промпта
    """
    version: str


class ChangelogItem(BaseModel):
    version: str
    date: str | None = None
    note: str


class Reasoning(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    mode: Literal["off", "low", "medium", "high", "provider_default"] | None = None
    max_output_tokens: int | None = None


class SkillManifest(BaseModel):
    """
    Манифест скилла или агента (skills/<name>/skill.yaml). ТЗ требует версионировать скиллы и агентов и хранить промпты и конфиги отдельными файлами.
    """

    name: str
    version: str = Field(..., pattern="^\\d+\\.\\d+\\.\\d+$")
    stage: Literal["analyze", "import", "story", "plan", "compose", "audit", "repair"]
    description: str
    model_role: Literal["llm", "vlm", "text_to_image", "none"] | None = None
    prompts: list[Prompt]
    params: dict[str, Any] | None = None
    """
    temperature, seed, лимиты, пороги
    """
    input_schema: str | None = None
    """
    какой контракт на входе
    """
    output_schema: str | None = None
    """
    какой контракт на выходе
    """
    changelog: list[ChangelogItem] | None = None
    schema_version: Literal["1.1"]
    reasoning: Reasoning | None = None
    response_format: Literal["json_schema", "json_object", "text"] | None = None


class Slide1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    slide_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    overrides: list[Override]


class SlidePatch(BaseModel):
    """
    Запрос ручных правок из визуального редактора к ревизии варианта (этап 22). У каждого перечисленного слайда список overrides заменяется целиком: пустой список возвращает слайд к сгенерированному виду; неперечисленные слайды не меняются. order — новый порядок всех слайдов варианта (перестановка slide_id без модели). Применяется детерминированно к SlidePlan базовой ревизии и создаёт новую ревизию тем же конвейером, что правка из чата (compose → export → audit), без вызова модели.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: Literal["1.0"]
    job_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    variant_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    base_revision: int = Field(..., ge=1)
    """
    Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
    """
    slides: list[Slide1]
    order: list[Id] | None = None
    """
    новый порядок слайдов: перестановка всех slide_id плана; отсутствует — порядок не меняется
    """
    template_logo: Literal["keep", "drop"] | None = None
    """
    снять или вернуть знак шаблона во всей колоде; отсутствует — не менять
    """


class Variant1(BaseModel):
    """
    Три варианта вёрстки одного контента различаются по одной заявленной оси. Ось и обоснование попадают в документацию и в интерфейс сравнения.
    """

    variant_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    axis: Literal["density", "layout_family", "visualization", "narrative_order", "custom"]
    value: str
    """
    например compact | balanced | detailed
    """
    rationale: str | None = None


class Story(BaseModel):
    purpose: str | None = None
    key_takeaway: str | None = None
    outline: list[str] | None = None


class SlideCount3(BaseModel):
    """
    Требование к числу слайдов, унаследованное из запроса: точное число или диапазон; план обязан ему соответствовать. target — целевое число слайдов варианта внутри диапазона (compact ближе к min, detailed к max)
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    exact: int | None = Field(None, ge=1)
    min: int | None = Field(None, ge=1)
    max: int | None = Field(None, ge=1)
    target: int | None = Field(None, ge=1)


class CoveredItem(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    thesis_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    slide_ids: list[Id]
    reduction: str | None = None


class Coverage1(BaseModel):
    """
    Покрытие обязательных тезисов StoryPlan: заполняется планировщиком, проверяется валидатором
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    required_thesis_ids: list[Id]
    covered: list[CoveredItem]
    missing: list[Id] | None = None


class Comparison(BaseModel):
    """
    Данные для сопоставления вариантов: последовательность паттернов и способы визуализации
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    pattern_sequence: list[Id] | None = None
    visual_kinds: list[str] | None = None
    text_chars_total: int | None = None


class SlideCount4(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    exact: int | None = Field(None, ge=1)
    min: int | None = Field(None, ge=1)
    max: int | None = Field(None, ge=1)


class EffectiveBrief(BaseModel):
    """
    бриф и явные настройки запроса, применённые до построения плана; смена любого поля меняет content_hash
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    purpose: Literal["feature", "product", "project", "initiative", "report", "other"] | None = None
    title: str | None = None
    audience: str | None = None
    goal: str | None = None
    language: str | None = None
    tone: str | None = None
    must_include: list[str] | None = None
    avoid: list[str] | None = None
    slide_count: SlideCount4 | None = None


class AllowedReduction(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    thesis_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    reduction: Literal[
        "drop_examples",
        "merge_with",
        "shorten_wording",
        "table_to_chart",
        "chart_to_number",
        "drop_entirely",
    ]
    target_thesis_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    для merge_with: с каким тезисом объединить
    """
    note: str | None = None


class MustKeepFacts(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    total: int = Field(..., ge=0)
    covered: int = Field(..., ge=0)


class MustIncludeItem(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    item: str
    thesis_ids: list[Id]


class Coverage2(BaseModel):
    """
    покрытие обязательного содержания тезисами: проверяется кодом до вёрстки
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    must_keep_facts: MustKeepFacts | None = None
    must_include: list[MustIncludeItem] | None = None
    """
    пункты brief.must_include и тезисы, которые их раскрывают; пустой список тезисов — пункт не покрыт
    """


class SourceFile(BaseModel):
    name: str
    size_bytes: int
    format: Literal["pptx"]


class Master(BaseModel):
    master_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    name: str | None = None
    theme_name: str | None = None


class Stats1(BaseModel):
    slides: int
    layouts: int
    masters: int
    media: int | None = None
    native_charts: int | None = None
    native_tables: int | None = None
    smartart: int | None = None
    embedded_fonts: int | None = None
    notes_with_text: int | None = None


class SampleSlide(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    slide_index: int = Field(..., ge=0)
    pptx_slide_part: str | None = None
    layout_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    classification: Literal[
        "content_sample", "style_guide", "asset_catalog", "empty", "hidden", "other"
    ]
    group_id: str | None = None
    """
    группа похожих образцов, отправленных в VLM вместе
    """
    preview_path: str | None = None
    confidence: float | None = Field(None, ge=0.0, le=1.0)


class DynamicField(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["slide_number", "date", "section_index", "total_slides"]
    appears_on: str
    element_ref: str | None = None


class Evidence(BaseModel):
    """
    измеренное значение и порог: например measured 7 буллетов при пороге 6
    """

    measured: Any | None = None
    threshold: Any | None = None
    details: str | None = None


class Fix(BaseModel):
    available: bool
    strategy: (
        Literal[
            "shrink_text",
            "rewrite_shorter",
            "split_slide",
            "change_pattern",
            "move_element",
            "resize_element",
            "recolor",
            "replace_font",
            "remove_placeholder",
            "regenerate_text",
            "regenerate_image",
            "add_labels",
            "remove_slide",
            "none",
        ]
        | None
    ) = None
    description: str | None = None
    cost: Literal["cheap", "llm", "rerender"] | None = None
    affects: (
        list[Literal["slide", "neighbors", "deck_order", "fact_coverage", "all_variants"]] | None
    ) = None


class Issue(BaseModel):
    issue_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    check_id: str
    slide_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    slide_index: int = Field(..., ge=0)
    severity: Literal["blocking", "error", "warning", "info"]
    """
    blocking мешает статусу succeeded
    """
    kind: Literal["deterministic", "contextual"]
    """
    способ проверки; серьёзность задаётся отдельно полем severity
    """
    message: str
    """
    понятная пользователю формулировка
    """
    bbox: Bbox | None = None
    """
    область для подсветки на миниатюре
    """
    element_ids: list[str] | None = None
    evidence: Evidence | None = None
    """
    измеренное значение и порог: например measured 7 буллетов при пороге 6
    """
    fix: Fix
    status: Literal["open", "selected", "fixed", "ignored", "unfixable"]
    revision: int | None = Field(None, ge=1)
    """
    Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
    """
    origin: Literal["generated", "template"] | None = None
    """
    template: дефект присутствует в исходном шаблоне
    """


class ChatFact(BaseModel):
    """
    значение из сообщения человека: подставляется как есть, в аудите подписано «по указанию пользователя»
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    fact_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    value: float | str
    unit: str | None = None
    period: str | None = None
    quote: str
    """
    цитата из сообщения
    """
    at: AwareDatetime | None = None


class Background1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["solid", "gradient", "image", "inherited"] | None = None
    color: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    """
    Цвет в формате #RRGGBB
    """
    asset_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """


class Target1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    object_id: str | None = None
    source_object_id: str | None = None
    slot_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """


class OverridesDroppedItem(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: str
    target: Target1 | None = None
    code: str
    message: str


class OutlineEntry(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    index: int = Field(..., ge=1)
    sld_id: int
    slide_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    role: str
    title: str
    kinds: list[str]
    """
    виды содержательных объектов слайда без повторов: текст, картинка, таблица, диаграмма, схема
    """
    hidden: bool | None = None
    edited: bool | None = None


class Background2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["solid", "gradient", "image", "inherited"]
    color: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    """
    Цвет в формате #RRGGBB
    """
    image_sha256: str | None = None


class SourceModel(BaseModel):
    """
    откуда снимок: ревизия варианта (variant), офисная копия (office) или файл без проекта (file)
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["variant", "office", "file"]
    job_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    variant_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    document_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    revision: int | None = Field(None, ge=1)
    """
    Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
    """
    pptx_sha256: str | None = Field(None, pattern="^[0-9a-f]{64}$")


class BriefDraft(BaseModel):
    """
    Черновик брифа в интерфейсе: пустая строка означает «не задано»; в ContentPackage уходит с умолчаниями
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    purpose: Literal["", "feature", "product", "project", "initiative", "report", "other"]
    title: str
    audience: str
    goal: str
    language: str
    tone: str
    must_include: list[str]
    avoid: list[str]


class SlideRef(BaseModel):
    """
    для сообщения пользователя: слайд, к которому обращена просьба (чип в поле ввода)
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    job_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    variant_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    revision: int = Field(..., ge=1)
    """
    Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
    """
    slide_index: int = Field(..., ge=0)


class Slide2(RootModel[int]):
    root: int = Field(..., ge=1)


class Event(BaseModel):
    """
    Событие ленты чата: сообщение пользователя или карточка шага. Карточка хранит только идентификаторы и читает живое состояние
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    event_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    at: AwareDatetime
    role: Literal["user", "assistant"]
    kind: Literal[
        "message",
        "text",
        "template_question",
        "template_card",
        "content_card",
        "brief_card",
        "job_card",
        "audit_card",
        "edit_card",
        "edit_result",
    ]
    text: str | None = None
    file_ids: list[Id] | None = None
    file_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    resolved: Literal["template", "material", "deck"] | None = None
    """
    ответ на вопрос о PPTX: шаблон оформления, материал с содержанием или deck — готовая презентация как результат (этап 21)
    """
    template_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    package_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    job_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    understood: list[str] | None = None
    missing_purpose: bool | None = None
    brief_source: Literal["model", "heuristic"] | None = None
    """
    для brief_card: чем извлечён бриф из сообщения — моделью или детерминированными правилами (резерв)
    """
    slide_ref: SlideRef | None = None
    """
    для сообщения пользователя: слайд, к которому обращена просьба (чип в поле ввода)
    """
    variant_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    edit_job_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    slide_index: int | None = Field(None, ge=0)
    document_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    для edit_result: офисная копия, которую изменила правка
    """
    revision: int | None = Field(None, ge=0)
    """
    для edit_result: ревизия копии после правки
    """
    base_revision: int | None = Field(None, ge=0)
    """
    для edit_result: ревизия копии до правки — к ней возвращает «Отменить»
    """
    slides: list[Slide2] | None = None
    """
    для edit_result: слайды правки (с единицы)
    """
    undone: bool | None = None
    """
    правка отменена («Отменить» в карточке или «отмени» словом)
    """


class SettingsDraft(BaseModel):
    """
    Настройки генерации в интерфейсе; в GenerationRequest переводятся при запуске
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    design_mode: Literal["template_only", "mixed", "all_new"] | None = None
    """
    Выбранный режим композиций. Без выбора генерация совместима со смешанным режимом.
    """
    mode: Literal["range", "exact"]
    min: int = Field(..., ge=1, le=60)
    max: int = Field(..., ge=1, le=60)
    exact: int = Field(..., ge=1, le=60)
    variants: list[Literal["compact", "balanced", "detailed"]]
    contextual: bool
    images: bool
    seed: int | None = None
    """
    отсутствие поля равно null
    """
    force: bool


class Thesis(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    thesis_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    order: int = Field(..., ge=1)
    kind: Literal["section", "claim", "evidence", "conclusion", "call_to_action", "context"]
    parent_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    тезис-раздел, к которому относится
    """
    statement: str
    """
    формулировка-вывод; может содержать {fact:<fact_id>}
    """
    explanation: str | None = None
    required: bool
    """
    обязателен во всех трёх вариантах
    """
    source_refs: list[Id] | None = None
    """
    block_id из ContentPackage
    """
    fact_refs: list[Id] | None = None
    dataset_refs: list[Id] | None = None
    asset_refs: list[Id] | None = None
    suggested_visual: (
        Literal[
            "text",
            "bullets",
            "number",
            "chart",
            "table",
            "diagram",
            "image",
            "quote",
            "comparison",
            "timeline",
        ]
        | None
    ) = None


class AssetModel(BaseModel):
    asset_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    kind: Literal[
        "image", "icon", "logo", "photo", "screenshot", "mockup", "chart_image", "qr", "background"
    ]
    media_path: str
    """
    путь внутри pptx, например ppt/media/image12.png
    """
    sha256: str
    width_px: int | None = None
    height_px: int | None = None
    source_slide_index: int | None = None
    bbox_on_source: Bbox | None = None
    tags: list[str] | None = None
    reusable: bool | None = None
    """
    можно ли использовать как пиктограмму или иллюстрацию в новых слайдах
    """


class ThemeFonts(BaseModel):
    major: str | None = None
    minor: str | None = None


class Margins(BaseModel):
    """
    поля слайда в долях ширины и высоты; выводятся из образцов
    """

    top: float
    right: float
    bottom: float
    left: float


class ColumnGrid(BaseModel):
    columns: int | None = None
    gutter: float | None = None


class Spacing(BaseModel):
    margins: Margins | None = None
    """
    поля слайда в долях ширины и высоты; выводятся из образцов
    """
    column_grid: ColumnGrid | None = None


class Shape(BaseModel):
    """
    Пластика шаблона: по ней собственные композиции повторяют вид плашек. Медианы по заметным фигурам образцов.
    """

    card_geometry: Literal["rect", "roundRect"] | None = None
    corner_ratio: float | None = Field(None, ge=0.0, le=0.5)
    """
    скругление в долях половины меньшей стороны
    """
    rounded_share: float | None = Field(None, ge=0.0, le=1.0)
    stroke_pt: float | None = Field(None, ge=0.0)
    shadow_share: float | None = Field(None, ge=0.0, le=1.0)
    confidence: float | None = Field(None, ge=0.0, le=1.0)


class FixedElement(BaseModel):
    """
    Элемент, который должен оставаться на месте: логотип, колонтитул, номер страницы, навигационные точки, фон
    """

    element_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    kind: Literal[
        "logo",
        "footer",
        "page_number",
        "background",
        "decoration",
        "navigation_dots",
        "qr_placeholder",
    ]
    bbox: Bbox
    appears_on: str
    """
    all | layout:<layout_id> | pattern:<pattern_id>
    """
    asset_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    must_not_move: bool | None = True
    element_ref: str | None = None
    source_part: str | None = None
    """
    часть пакета, где живёт элемент: мастер, макет или слайд
    """


class Guide(BaseModel):
    orientation: Literal["horizontal", "vertical"]
    pos: float = Field(..., ge=0.0, le=1.0)
    source: Literal["view_props", "inferred"]


class Guideline(BaseModel):
    """
    Правило оформления, найденное текстом в самом шаблоне, например «Перекрытие рядов ±50%, без линий сетки»
    """

    text: str
    source_slide_index: int | None = None
    kind: Literal["typography", "color", "chart", "table", "icons", "layout", "general"]


class Placeholder(BaseModel):
    idx: int
    type: Literal[
        "title",
        "ctrTitle",
        "subTitle",
        "body",
        "obj",
        "pic",
        "chart",
        "tbl",
        "dt",
        "ftr",
        "sldNum",
        "other",
    ]
    bbox: Bbox | None = None
    font: FontSpec | None = None


class Layout(BaseModel):
    layout_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    name: str
    """
    имя из cSld@name, например «2 фактоида + текст»
    """
    master_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    placeholders: list[Placeholder]
    sample_slide_count: int | None = None
    """
    сколько образцовых слайдов шаблона используют этот макет
    """


class Source1(BaseModel):
    """
    Откуда взят паттерн: образцовый слайд шаблона, его макет или собственная композиция библиотеки, построенная из дизайн-кода шаблона на его макете.
    """

    kind: Literal["sample_slide", "layout", "builtin"]
    slide_index: int | None = None
    layout_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    pptx_slide_part: str | None = None
    composition_id: str | None = None
    """
    идентификатор композиции собственной библиотеки (source.kind = builtin)
    """


class Constraints(BaseModel):
    min_items: int | None = None
    max_items: int | None = None
    supports: list[Literal["image", "icon", "table", "chart", "diagram", "number", "qr"]] | None = (
        None
    )


class SequenceHints(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    typical_position: Literal["first", "early", "middle", "late", "last", "any"] | None = None
    max_consecutive: int | None = None


class Tone(BaseModel):
    """
    тон фона образца: заливка слайда, закрывающая картинка или фигура, фон макета, мастера или lt1 темы; порог относительной яркости 0,5
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    background: Literal["light", "dark", "unknown"]
    luminance: float | None = Field(None, ge=0.0, le=1.0)
    source: str
    """
    slide_fill, slide_picture, slide_shape, layout_fill, layout_picture, layout_shape, master_fill, master_picture, theme, none
    """


class Fragment(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    text: str
    """
    слово или фраза в тексте объекта
    """
    paragraph: int | None = Field(None, ge=1)


class Cell(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    row: int = Field(..., ge=1)
    col: int = Field(..., ge=1)


class Paragraph(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    text: str | None = None
    level: int | None = None
    bullet: bool | None = None
    align: Literal["left", "center", "right", "justify"] | None = None
    style: ComputedTextStyle | None = None


class Insets1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    left: float | None = None
    top: float | None = None
    right: float | None = None
    bottom: float | None = None


class Text(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    plain: str | None = None
    paragraphs: list[Paragraph] | None = None
    computed_style: ComputedTextStyle | None = None
    insets: Insets1 | None = None
    autofit: Literal["none", "shrink", "resize_shape"] | None = None
    anchor: Literal["top", "middle", "bottom"] | None = None
    """
    вертикальная привязка текста в рамке (a:bodyPr@anchor): по ней текст стоит там же, где в PowerPoint
    """
    fact_refs: list[Id] | None = None


class Crop(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    left: float | None = None
    top: float | None = None
    right: float | None = None
    bottom: float | None = None


class Picture1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    asset_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    crop: Crop | None = None
    natural_width_px: int | None = None
    natural_height_px: int | None = None
    fit: Literal["cover", "contain", "as_is"] | None = None
    origin: Literal["template", "content", "generated", "icon_library"] | None = None
    recolored: bool | None = None


class Table(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    rows: int | None = None
    cols: int | None = None
    dataset_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    header_row: bool | None = None
    row_offset: int | None = Field(None, ge=0)
    truncated: bool | None = None
    """
    набор данных не поместился целиком на этот слайд
    """


class Chart(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    chart_part: str | None = None
    type: str | None = None
    series_count: int | None = None
    dataset_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    has_legend: bool | None = None
    has_axis_titles: bool | None = None
    has_data_labels: bool | None = None
    units: str | None = None
    categories_count: int | None = None
    built: Literal["replaced", "rebuilt", "added"] | None = None
    """
    replaced — данные подставлены в диаграмму образца; rebuilt — образец заменён новой; added — построена на месте картинки или плейсхолдера
    """


class Fill(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["none", "solid", "gradient", "image", "inherited"] | None = None
    color: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    """
    Цвет в формате #RRGGBB
    """
    source: StyleSource | None = None


class Line(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    color: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    """
    Цвет в формате #RRGGBB
    """
    width_pt: float | None = None


class Fit(BaseModel):
    """
    измерение из плана (blocks[].fit): выбранный кегль и действие лестницы ёмкости
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    size_pt: float | None = None
    slot_size_pt: float | None = None
    lines: int | None = None
    max_lines: int | None = None
    action: str | None = None


class Diagram(BaseModel):
    """
    схема из фигур: группа-контейнер и её узлы (не SmartArt)
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    kind: str | None = None
    node_ids: list[str] | None = None


class Paragraph1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    text: str
    level: int = Field(..., ge=0)
    bullet: bool


class Text1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    plain: str
    paragraphs: list[Paragraph1]


class Placeholder1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: str
    idx: int | None = None
    inherited_geometry: bool
    """
    своей рамки на слайде нет, рамка — из макета или мастера
    """


class Slot1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    slot_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    slot_kind: str | None = None
    source_object_id: str | None = None


class MergedItem(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    row: int = Field(..., ge=1)
    col: int = Field(..., ge=1)
    rows: int = Field(..., ge=1)
    cols: int = Field(..., ge=1)


class Table1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    rows: list[list[str]]
    """
    тексты ячеек строками; у объединённых ячеек текст в первой, остальные пустые
    """
    merged: list[MergedItem] | None = None
    """
    объединения: первая ячейка (с единицы) и сколько строк и столбцов она занимает
    """


class Series(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    name: str
    values: list[float | None]


class Chart1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    type: str
    categories: list[str]
    series: list[Series]
    values_from: Literal["cache", "workbook"] | None = None
    """
    откуда значения: кэш части chart или встроенная книга
    """


class Picture2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    sha256: str | None = None
    width_px: int | None = None
    height_px: int | None = None
    asset_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """


class Number(BaseModel):
    fact_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    format: str | None = None
    """
    например «{value} %» или «{value} млн ₽»
    """


class Table2(BaseModel):
    dataset_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    columns: list[str] | None = None
    max_rows: int | None = None
    highlight_row: int | None = None
    row_offset: int | None = Field(None, ge=0)
    """
    с какой строки набора данных начинается таблица этого слайда; большие наборы планировщик делит между слайдами
    """


class Chart2(BaseModel):
    """
    Нативная диаграмма PowerPoint; стиль берётся из палитры и правил шаблона
    """

    type: Literal["column", "bar", "stacked_column", "line", "area", "pie", "doughnut", "scatter"]
    dataset_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    category_column: str | None = None
    series: list[str] = Field(..., max_length=5)
    title: str | None = None
    units: str | None = None
    show_legend: bool | None = None
    show_axis_labels: bool | None = None
    show_data_labels: bool | None = None


class Generate(BaseModel):
    """
    задача со звёздочкой: генерация изображения моделью text-to-image
    """

    prompt: str
    negative_prompt: str | None = None
    style: str | None = None


class Image(BaseModel):
    asset_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    generate: Generate | None = None
    """
    задача со звёздочкой: генерация изображения моделью text-to-image
    """
    fit: Literal["cover", "contain"] | None = None
    alt: str | None = None


class Fit1(BaseModel):
    """
    Измерение текста блока по метрикам шрифта после подстановки фактов: выбранный кегль и что сделала лестница ёмкости
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    size_pt: float = Field(..., ge=1.0)
    """
    кегль, с которым текст помещается; равен кеглю слота, если уменьшать не пришлось
    """
    slot_size_pt: float | None = Field(None, ge=1.0)
    lines: int = Field(..., ge=0)
    max_lines: int = Field(..., ge=0)
    chars: int | None = Field(None, ge=0)
    action: Literal["as_is", "pattern_swap", "font_step", "shortened", "split", "overflow"]
    """
    overflow — текст не помещается и после лестницы; такой план не выдаётся при точном числе слайдов
    """
    note: str | None = None


class RuleScope(BaseModel):
    """
    Область действия правила: где найдено и к чему применимо. Случайный цвет на служебном слайде не становится разрешением использовать его везде.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    level: Literal["theme", "master", "layout", "pattern", "text_role", "slide"]
    pattern_ids: list[Id] | None = None
    text_roles: (
        list[
            Literal[
                "display", "title", "subtitle", "body", "caption", "kpi", "label", "code", "other"
            ]
        ]
        | None
    ) = None
    slide_indexes: list[int] | None = None
    applies_to_new_content: bool | None = None
    """
    можно ли использовать в новых слайдах
    """


class MeasuredWith(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    font_file: str | None = None
    method: Literal["font_metrics", "heuristic"] | None = None
    margin_ratio: float | None = None


class Capacity(BaseModel):
    max_chars: int | None = None
    max_lines: int | None = None
    max_items: int | None = None
    max_words_per_item: int | None = None
    measured_with: MeasuredWith | None = None
    confidence: float | None = Field(None, ge=0.0, le=1.0)


class Crop1(BaseModel):
    """
    crop исходной картинки в долях, для слотов image
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    left: float | None = None
    top: float | None = None
    right: float | None = None
    bottom: float | None = None


class Slot(BaseModel):
    """
    Область паттерна под содержание. Ёмкость считается по метрикам шрифта, а не на глаз.
    """

    slot_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    kind: Literal[
        "title",
        "subtitle",
        "body",
        "bullets",
        "number",
        "label",
        "caption",
        "date",
        "name",
        "position",
        "image",
        "icon",
        "table",
        "chart",
        "diagram",
        "qr",
        "code",
        "footer",
    ]
    bbox: Bbox
    z_order: int | None = None
    font: FontSpec | None = None
    align: Literal["left", "center", "right", "justify"] | None = None
    valign: Literal["top", "middle", "bottom"] | None = None
    capacity: Capacity | None = None
    required: bool | None = False
    repeat_group: str | None = None
    """
    идентификатор группы повторяющихся карточек; слоты одной группы клонируются вместе
    """
    sample_text: str | None = None
    element_ref: str | None = None
    """
    id объекта в исходном образцовом слайде (p:cNvPr@id); вёрстка заменяет содержимое именно этого объекта
    """
    computed_style: ComputedTextStyle | None = None
    paragraph_params: ParagraphParams | None = None
    group_path: list[str] | None = None
    """
    идентификаторы групп от внешней к внутренней, если объект вложен
    """
    rotation_deg: float | None = None
    crop: Crop1 | None = None
    """
    crop исходной картинки в долях, для слотов image
    """


class GroupPathItem(RootModel[str]):
    root: str = Field(..., pattern="^[0-9]{1,20}$")


class ObjectRef(BaseModel):
    """
    объект снимка; slot_id и source_object_id — охрана адреса в плане, как у target правки override
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    object_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    p:cNvPr@id объекта снимка; у надписи, созданной этими же операциями (object.add_text), — её new_object_id
    """
    group_path: list[GroupPathItem] | None = None
    slot_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    source_object_id: str | None = None


class SlideRef1(BaseModel):
    """
    слайд: номер и sld_id ревизии снимка; у плана — slide_id
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    index: int = Field(..., ge=1)
    sld_id: int | None = None
    slide_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """


class SlideRef2(BaseModel):
    """
    слайд: номер и sld_id ревизии снимка; у плана — slide_id
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    index: int | None = Field(None, ge=1)
    sld_id: int | None = None
    slide_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """


class Address(BaseModel):
    """
    адрес объекта на слайде: p:cNvPr@id и путь групп от внешней к внутренней (пустой — объект верхнего уровня)
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    object_id: str = Field(..., pattern="^[0-9]{1,20}$")
    group_path: list[GroupPathItem] | None = None


class Icon(BaseModel):
    asset_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    пиктограмма из ресурсов шаблона
    """
    query: str | None = None
    """
    поисковый запрос к библиотеке иконок, если в шаблоне нет подходящей
    """
    color: str | None = Field(None, pattern="^#[0-9A-Fa-f]{6}$")
    """
    Цвет в формате #RRGGBB
    """


class Deck(BaseModel):
    pptx_artifact: str
    slide_count: int
    renderer: VersionRef | None = None
    thumbnail_width_px: int | None = None
    pptx_hash: str | None = None
    composed_deck_artifact: str | None = None
    fonts: list[Font] | None = None


class ContextualAnswer(BaseModel):
    slide_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    slide_index: int | None = None
    question_id: int = Field(..., ge=1, le=11)
    question: str | None = None
    answer: Literal["yes", "no", "unsure"]
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    explanation: str | None = None
    model: ModelRef | None = None
    prompt: VersionRef | None = None
    inputs: list[str] | None = None
    outcome: Literal["passed", "failed", "not_applicable", "not_checked"] | None = None


class Result(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    check_id: str
    scope: Literal["slide", "deck"]
    slide_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    slide_index: int | None = Field(None, ge=0)
    outcome: Literal["passed", "failed", "not_applicable", "not_checked"]
    reason: str | None = None
    """
    обязательна для not_applicable и not_checked
    """
    issue_ids: list[Id] | None = None
    duration_ms: int | None = None


class RecheckedAfterRepair(BaseModel):
    """
    Что повторно проверено после исправления и почему
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    base_revision: int | None = Field(None, ge=1)
    """
    Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
    """
    changed_slide_ids: list[Id] | None = None
    dependent_slide_ids: list[Id] | None = None
    """
    соседи и слайды, затронутые порядком или покрытием фактов
    """
    deck_checks_rerun: list[str] | None = None


class AuditReport(BaseModel):
    """
    Отчёт аудита одной ревизии одного варианта. Реестр проверок повторяет Приложение 1 ТЗ и расширяется своими проверками. Версия 1.1: результат каждой проверки по области passed/failed/not_applicable/not_checked с причиной; серьёзность отдельно от способа; ревизия; входы контекстных проверок; покрытие и зависимые повторные проверки.
    """

    schema_version: Literal["1.1"]
    report_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    job_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    variant_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    created_at: AwareDatetime
    deck: Deck
    checks: list[Check]
    """
    Что запускалось. Список целиком, включая проверки без находок: из него собирается AUDIT.md.
    """
    issues: list[Issue]
    contextual_answers: list[ContextualAnswer] | None = None
    """
    Ответы модели на 11 вопросов валидации контента по картинке слайда
    """
    summary: Summary
    metrics: Metrics | None = None
    revision: int = Field(..., ge=1)
    """
    Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
    """
    results: list[Result]
    """
    Результат каждой проверки для каждой области (слайд или колода), включая пройденные и неприменимые
    """
    coverage: Coverage
    rechecked_after_repair: RecheckedAfterRepair | None = None
    """
    Что повторно проверено после исправления и почему
    """


class BriefExtract(BaseModel):
    """
    Ответ POST /api/brief: бриф и настройки, извлечённые из свободного сообщения чата. Запрос описан в $defs/request. Поля, которых нет в тексте, не заполняются; understood перечисляет найденные. Ответ — предложение для подтверждения пользователем, а не решение. Версия 1.2.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: Literal["1.2"]
    brief: Brief
    slide_count: SlideCount | None = None
    variants: list[Literal["compact", "balanced", "detailed"]] | None = None
    understood: list[str]
    """
    какие поля действительно найдены в тексте: purpose, title, audience, goal, tone, language, must_include, avoid, slide_count, variants
    """
    intent: Literal["generate", "edit", "none"]
    """
    generate: явная команда запустить генерацию; edit: правка готовых слайдов; none: описание задачи или ничего
    """
    source: Literal["model", "heuristic"]
    """
    model: извлечено моделью; heuristic: детерминированные правила без модели
    """
    model: ModelRef | None = None


class Base(BaseModel):
    """
    ревизия, к снимку которой относятся адреса
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["variant", "office"]
    job_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    variant_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    document_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    revision: int = Field(..., ge=1)
    """
    Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
    """


class Error(BaseModel):
    """
    Ошибка задания или операции API
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    code: str
    message: str
    stage: (
        Literal[
            "queued",
            "analyze",
            "import",
            "story",
            "plan",
            "compose",
            "export",
            "audit",
            "repair",
            "finalize",
            "done",
        ]
        | None
    ) = None
    retryable: bool | None = None
    details: dict[str, Any] | None = None


class GenerationMeta(BaseModel):
    """
    Как был создан документ моделью: версии скиллов и промптов, модели, параметры, usage
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    skills: list[VersionRef]
    prompts: list[VersionRef] | None = None
    models: list[ModelRef]
    seed: int | None = None
    temperature: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cache_hit: bool | None = None
    created_at: AwareDatetime | None = None


class StageTiming(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    stage: Literal[
        "queued",
        "analyze",
        "import",
        "story",
        "plan",
        "compose",
        "export",
        "audit",
        "repair",
        "finalize",
        "done",
    ]
    variant_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    status: Literal["pending", "running", "done", "failed", "skipped"]
    started_at: AwareDatetime | None = None
    duration_ms: int | None = None
    quota_wait_ms: int | None = None
    """
    ожидание лимитера провайдера внутри этапа
    """
    cache_hit: bool | None = None


class HtmlSupport(BaseModel):
    """
    границы поддержки HTML-экспорта для этой колоды и причины резервного рендера
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    full_native: bool
    supported_kinds: (
        list[
            Literal[
                "text",
                "picture",
                "table",
                "chart",
                "shape",
                "connector",
                "group",
                "placeholder_empty",
                "other",
            ]
        ]
        | None
    ) = None
    fallback_elements: list[FallbackElement]


class Variant(BaseModel):
    variant_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    axis: str
    value: str
    rationale: str | None = None
    status: Literal["pending", "running", "ready", "needs_review", "failed"]
    slide_count: int | None = None
    plan_artifact: str | None = None
    audit_artifact: str | None = None
    audit_summary: dict[str, Any] | None = None
    artifacts: Artifacts | None = None
    revision: int = Field(..., ge=1)
    """
    Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
    """
    revisions: list[Revision1] | None = None
    """
    история ревизий варианта
    """
    audit: Audit | None = None
    error: Error | None = None
    composed_deck_artifact: str | None = None
    ready_at: AwareDatetime | None = None
    """
    момент готовности файлов варианта
    """
    audited_at: AwareDatetime | None = None
    stages: list[StageTiming] | None = None


class Metrics1(BaseModel):
    stages: list[StageTiming] | None = None
    llm_calls: list[LlmCall] | None = None
    totals: Totals
    cost_estimate: CostEstimate | None = None
    queue_wait_ms: int | None = None
    quota_wait_ms: int | None = None
    """
    суммарное ожидание лимитера провайдера
    """
    retries: int | None = None
    cache: Cache1 | None = None
    timeline: Timeline | None = None
    """
    ключевые моменты от принятия задания
    """


class GenerationResult(BaseModel):
    """
    Результат задания генерации. Отдаётся по ссылке из JobStatus и напрямую GET /api/generations/{id}; пока задание идёт, поля вариантов заполняются по мере готовности. Версия 1.3 (этап 22): у правок edits[] origin — источник (chat: инструкция модели, editor: ручные правки визуального редактора) и summary — сводка правок. Версия 1.1: ревизии, режим исполнения слоёв, частичные результаты, полнота аудита, версии рендерера и шрифтов, ожидание очереди и квоты, повторы, кэши, время первого и всех готовых вариантов.
    """

    schema_version: Literal["1.3"]
    job_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    status: Literal["queued", "running", "succeeded", "needs_review", "failed", "canceled"]
    """
    succeeded: обязательные проверки завершены и блокирующих проблем нет; needs_review: результат пригоден к просмотру, но есть находки или неполный аудит
    """
    stage: Literal[
        "queued",
        "analyze",
        "import",
        "story",
        "plan",
        "compose",
        "export",
        "audit",
        "repair",
        "finalize",
        "done",
    ]
    progress: Progress | None = None
    template_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    package_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    request: GenerationRequest | None = None
    created_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    variants: list[Variant]
    artifacts_manifest: dict[str, ArtifactsManifest] | None = None
    """
    Единственный источник разрешённых имён для GET /api/jobs/{id}/artifacts/{name}
    """
    metrics: Metrics1
    versions: Versions
    """
    Всё, что нужно для воспроизведения: версии приложения, скиллов, промптов, моделей и рендерера
    """
    repairs: list[Repair] | None = None
    warnings: list[Warning] | None = None
    error: Error | None = None
    story_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    execution_mode: ExecutionMode
    partial: bool | None = None
    """
    часть вариантов не готова или завершилась ошибкой; готовые доступны
    """
    edits: list[Edit] | None = None
    """
    правки слайдов по запросу из чата и из визуального редактора: каждая применённая правка создаёт ревизию варианта, как исправление
    """


class JobStatus(BaseModel):
    """
    Общее состояние любого задания: анализ шаблона, импорт содержания, генерация, исправление, правка слайда по запросу, ручные правки из визуального редактора (slide_patch). Отдаётся GET /api/jobs/{id}. Ссылка на результат ведёт на документ своего вида; задания анализа и импорта не заполняют GenerationResult.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: Literal["1.3"]
    job_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    kind: Literal[
        "template_analysis", "content_import", "generation", "repair", "slide_edit", "slide_patch"
    ]
    status: Literal["queued", "running", "succeeded", "needs_review", "failed", "canceled"]
    """
    succeeded: обязательные проверки завершены и блокирующих проблем нет; needs_review: результат пригоден к просмотру, но есть находки или неполный аудит
    """
    stage: (
        Literal[
            "queued",
            "analyze",
            "import",
            "story",
            "plan",
            "compose",
            "export",
            "audit",
            "repair",
            "finalize",
            "done",
        ]
        | None
    ) = None
    stages: list[StageTiming] | None = None
    """
    пройденные и текущие этапы с временем; для генерации разбивка по вариантам живёт в GenerationResult
    """
    progress: Progress | None = None
    created_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    queue_wait_ms: int | None = None
    """
    ожидание свободного воркера
    """
    depends_on: list[Id] | None = None
    parent_job_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    для исправлений и правок слайда: задание генерации
    """
    result: Result1 | None = None
    """
    ссылка на результат своего вида; заполняется по мере готовности
    """
    error: Error | None = None
    warnings: list[Warning] | None = None


class Project(BaseModel):
    """
    Проект — одна презентация: выбранный шаблон, файлы, бриф, настройки, задание генерации и лента событий чата. Серверная сущность: интерфейс восстанавливает проект по идентификатору из URL. Карточки ленты ссылаются на шаблоны, пакеты, задания и файлы по идентификаторам и не дублируют данные. Версия 1.3 (этап 6): карточка брифа хранит источник извлечения (модель или правила). Версия 1.2: серверная сущность проекта.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: Literal["1.6"]
    project_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    title: str = Field(..., max_length=200, min_length=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime
    template_id: Id | None = None
    """
    Отсутствие поля равно null.
    """
    package_id: Id | None = None
    """
    Отсутствие поля равно null.
    """
    job_id: Id | None = None
    """
    текущее задание генерации Отсутствие поля равно null.
    """
    chosen_variant: Id | None = None
    """
    Отсутствие поля равно null.
    """
    brief: BriefDraft
    settings: SettingsDraft
    files: list[ProjectFile]
    events: list[Event]


class StoryPlan(BaseModel):
    """
    Общий смысловой план презентации, создаётся один раз из ContentPackage и не зависит от шаблона и геометрии. Три SlidePlan ссылаются на него и обязаны покрыть все обязательные тезисы. Версия 1.2 (этап 6): effective_brief — бриф и явные настройки запроса, применённые до построения плана (язык, аудитория, цель, обязательные тезисы, ограничения, число слайдов); coverage — покрытие обязательных фактов и пунктов брифа тезисами; content_hash считается по нормализованному содержанию пакета, effective_brief, модели, промпту и схеме, без идентификаторов заданий и времени.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: Literal["1.2"]
    story_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    package_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    content_hash: str
    """
    sha256 нормализованного содержания ContentPackage вместе с effective_brief, моделью, версией скилла/промпта и схемой ответа; ключ кэша StoryPlan. Не зависит от package_id, job_id и времени
    """
    language: str
    purpose: Literal["feature", "product", "project", "initiative", "report", "other"]
    audience: str | None = None
    goal: str | None = None
    effective_brief: EffectiveBrief | None = None
    """
    бриф и явные настройки запроса, применённые до построения плана; смена любого поля меняет content_hash
    """
    key_takeaway: str
    """
    главный вывод всей презентации одним предложением
    """
    theses: list[Thesis] = Field(..., min_length=1)
    allowed_reductions: list[AllowedReduction] | None = None
    """
    что допустимо сокращать в компактных вариантах без потери обязательного содержания
    """
    assumptions: list[str] | None = None
    """
    допущения режима брифа, отделённые от подтверждённых данных
    """
    coverage: Coverage2 | None = None
    """
    покрытие обязательного содержания тезисами: проверяется кодом до вёрстки
    """
    generation_meta: GenerationMeta
    warnings: list[Warning] | None = None


class PaletteItem(BaseModel):
    hex: str = Field(..., pattern="^#[0-9A-Fa-f]{6}$")
    """
    Цвет в формате #RRGGBB
    """
    role: Literal[
        "primary", "secondary", "accent", "neutral", "background", "text", "muted", "warning"
    ]
    usage_count: int | None = None
    source: Literal["theme", "slides", "layouts", "master"] | None = None
    scope: RuleScope | None = None
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    style_source: StyleSource | None = None


class Colors(BaseModel):
    theme: dict[str, Color]
    """
    цвета темы мастера: dk1, lt1, dk2, lt2, accent1..accent6, hlink
    """
    palette: list[PaletteItem]


class Font4(BaseModel):
    family: str
    usage_count: int
    embedded: bool
    available_in_renderer: bool | None = None
    fallback: str | None = None
    roles: list[Literal["title", "body", "code", "decorative"]] | None = None
    scope: RuleScope | None = None
    file: str | None = None
    """
    фактический файл шрифта в рендерере, по которому измеряется текст
    """


class ScaleItem(BaseModel):
    size_pt: float
    role: Literal["display", "title", "subtitle", "body", "caption", "kpi", "other"]
    usage_count: int | None = None
    scope: RuleScope | None = None
    confidence: float | None = Field(None, ge=0.0, le=1.0)


class Typography(BaseModel):
    theme_fonts: ThemeFonts | None = None
    fonts: list[Font4]
    scale: list[ScaleItem]
    """
    типографическая шкала шаблона; кегли не из шкалы считаются нарушением
    """
    max_font_families: int | None = 2


class DesignTokens(BaseModel):
    colors: Colors
    typography: Typography
    spacing: Spacing | None = None
    shape: Shape | None = None
    """
    Пластика шаблона: по ней собственные композиции повторяют вид плашек. Медианы по заметным фигурам образцов.
    """


class Pattern(BaseModel):
    """
    Композиционный паттерн. Источник: образцовый слайд шаблона, макет или собственная композиция библиотеки. Паттерны шаблона в отборе идут первыми, собственные подключаются, когда шаблон не покрывает нужную подачу или вместимость.
    """

    pattern_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    name: str | None = None
    role: Literal[
        "title",
        "agenda",
        "section_divider",
        "bullets",
        "text",
        "two_column",
        "cards",
        "kpi",
        "numbers",
        "table",
        "chart",
        "timeline",
        "process",
        "comparison",
        "quote",
        "team",
        "speaker",
        "screenshot",
        "mockup",
        "pricing",
        "code",
        "image_full",
        "qr",
        "thanks",
        "freeform",
    ]
    source: Source1
    """
    Откуда взят паттерн: образцовый слайд шаблона, его макет или собственная композиция библиотеки, построенная из дизайн-кода шаблона на его макете.
    """
    slots: list[Slot]
    constraints: Constraints | None = None
    tags: list[str] | None = None
    preview_path: str | None = None
    """
    миниатюра исходного слайда
    """
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    role_source: Literal["heuristic", "vlm", "layout_name", "manual"] | None = None
    notes: str | None = None
    static_object_ids: list[str] | None = None
    """
    объекты образца, которые остаются как есть: декор, фон, логотипы
    """
    removable_object_ids: list[str] | None = None
    """
    объекты образца, удаляемые при незаполненных слотах, например лишние карточки
    """
    sequence_hints: SequenceHints | None = None
    group_id: str | None = None
    """
    группа образцов одной сигнатуры (роль, состав слотов, число карточек): члены взаимозаменяемы, планировщик подставляет их в сериях одинаковых композиций
    """
    tone: Tone | None = None
    """
    тон фона образца: заливка слайда, закрывающая картинка или фигура, фон макета, мастера или lt1 темы; порог относительной яркости 0,5
    """
    style_key: str | None = None
    """
    стиль служебного слайда вида «тон|семейство макета|photo или plain»: внутри колоды титул, разделители и финал берутся одного стиля, варианты — разных
    """
    chart_parts: list[str] | None = None
    """
    объекты нарисованной диаграммы образца (столбики, подписи значений и категорий): при заполнении слота chart вёрстка строит нативную диаграмму и убирает их
    """


class AddressModel(BaseModel):
    """
    адрес операции в снимке, к которому относится документ; scope говорит, что именно адресовано
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    scope: Literal[
        "object", "objects", "paragraph", "fragment", "cell", "slide", "slides", "deck", "none"
    ]
    slide: SlideRef1 | SlideRef2 | None = None
    """
    слайд: номер и sld_id ревизии снимка; у плана — slide_id
    """
    slides: list[SlideRef1 | SlideRef2] | None = Field(None, min_length=1)
    object: ObjectRef | None = None
    objects: list[ObjectRef] | None = Field(None, min_length=1)
    paragraph: int | None = Field(None, ge=1)
    """
    номер абзаца объекта
    """
    fragment: Fragment | None = None
    cell: Cell | None = None


class Object(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    object_id: str
    """
    p:cNvPr@id внутри слайда
    """
    name: str | None = None
    kind: Literal[
        "text",
        "picture",
        "table",
        "chart",
        "shape",
        "connector",
        "group",
        "placeholder_empty",
        "other",
    ]
    bbox: Bbox
    rotation_deg: float | None = None
    geometry: str | None = None
    """
    форма фигуры (a:prstGeom@prst): rect, roundRect, ellipse…; custom — произвольная геометрия (a:custGeom), её нельзя рисовать прямоугольником
    """
    geometry_adjust: float | None = Field(None, ge=0.0, le=0.5)
    """
    скругление углов (a:prstGeom/a:avLst «adj») долей от меньшей стороны фигуры: в PowerPoint радиус считается от неё, а не от каждой стороны отдельно
    """
    z_order: int
    group_path: list[str] | None = None
    """
    идентификаторы групп от внешней к внутренней
    """
    slot_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    слот плана, из которого пришло содержимое
    """
    source_object_id: str | None = None
    """
    объект образцового слайда шаблона
    """
    role: Literal["content", "fixed", "decoration", "background"] | None = None
    text: Text | None = None
    picture: Picture1 | None = None
    table: Table | None = None
    chart: Chart | None = None
    fill: Fill | None = None
    line: Line | None = None
    content_source: Literal["plan", "sample", "template", "generated", "user"] | None = None
    """
    plan — содержимое из блока плана; sample — намеренно оставленный текст или картинка образца (крошечный слот, незаполненный слот изображения), аудит не считает его заглушкой; template — статика образца, макета или мастера; generated — объект, построенный композером (диаграмма, таблица, схема); user — содержимое заменено ручной правкой из визуального редактора
    """
    user_overrides: list[Override] | None = None
    """
    ручные правки, применённые к этому объекту в этой ревизии
    """
    slot_kind: str | None = None
    """
    вид слота профиля, из которого пришёл объект
    """
    block_kind: str | None = None
    """
    вид блока плана; отличается от slot_kind у диаграммы в слоте image
    """
    fit: Fit | None = None
    """
    измерение из плана (blocks[].fit): выбранный кегль и действие лестницы ёмкости
    """
    diagram: Diagram | None = None
    """
    схема из фигур: группа-контейнер и её узлы (не SmartArt)
    """


class ObjectModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    address: Address
    name: str
    """
    p:cNvPr@name; сохраняется при сохранении в ONLYOFFICE, им редактор называет выделенную фигуру
    """
    kind: Literal[
        "text",
        "picture",
        "table",
        "chart",
        "shape",
        "connector",
        "group",
        "placeholder_empty",
        "other",
    ]
    role: Literal[
        "title",
        "subtitle",
        "body",
        "bullets",
        "number",
        "label",
        "caption",
        "date",
        "name",
        "position",
        "image",
        "icon",
        "table",
        "chart",
        "diagram",
        "qr",
        "code",
        "footer",
        "logo",
        "page_number",
        "decoration",
        "background",
        "group",
        "other",
    ]
    """
    роль объекта: виды слотов профиля шаблона и служебные (логотип, номер слайда, декор, фон, группа)
    """
    text: Text1 | None = None
    bbox: Bbox
    """
    рамка без поворота в долях слайда, с учётом групп
    """
    rotation_deg: float | None = None
    placeholder: Placeholder1 | None = None
    style: FontSpec | None = None
    """
    вычисленный шрифт первого непустого фрагмента: гарнитура, кегль, начертание, цвет
    """
    fixed: bool
    """
    фиксированный элемент шаблона: логотип, номер, колонтитул, навигация
    """
    slot: Slot1 | None = None
    table: Table1 | None = None
    chart: Chart1 | None = None
    picture: Picture2 | None = None
    hidden: bool | None = None


class Item(BaseModel):
    text: str
    icon: Icon | None = None
    fact_refs: list[Id] | None = None


class Item1(BaseModel):
    text: str
    sub: str | None = None
    icon: Icon | None = None


class Diagram1(BaseModel):
    """
    Схема из нативных фигур: замена SmartArt
    """

    kind: Literal[
        "process", "cycle", "pyramid", "hierarchy", "matrix", "funnel", "timeline", "venn"
    ]
    items: list[Item1]
    direction: Literal["horizontal", "vertical"] | None = None


class BlockModel(BaseModel):
    """
    Содержание одного слота. Ровно одно из полей содержания должно соответствовать kind слота; исключение — блок chart в слоте image паттерна с ролью chart.
    """

    slot_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    kind: Literal[
        "title",
        "subtitle",
        "body",
        "bullets",
        "number",
        "label",
        "caption",
        "date",
        "name",
        "position",
        "image",
        "icon",
        "table",
        "chart",
        "diagram",
        "qr",
        "code",
    ]
    text: str | None = None
    """
    может содержать {fact:<fact_id>}; композер подставляет значение из реестра
    """
    items: list[Item] | None = None
    number: Number | None = None
    table: Table2 | None = None
    chart: Chart2 | None = None
    """
    Нативная диаграмма PowerPoint; стиль берётся из палитры и правил шаблона
    """
    image: Image | None = None
    icon: Icon | None = None
    diagram: Diagram1 | None = None
    """
    Схема из нативных фигур: замена SmartArt
    """
    source_refs: list[Id] | None = None
    fact_refs: list[Id] | None = None
    fit: Fit1 | None = None
    """
    Измерение текста блока по метрикам шрифта после подстановки фактов: выбранный кегль и что сделала лестница ёмкости
    """


class TemplateProfile(BaseModel):
    """
    Результат слоя парсинга: дизайн-система, фиксированные элементы, ресурсы и композиционные паттерны шаблона. Полный профиль читают вёрстка и аудит; в модель уходит только llm_digest и выдержки по выбранным паттернам. Версия 1.1: области действия правил, вычисленные стили с источником, геометрия групп и crop, ссылки на объекты слотов, статические и динамические элементы, параметры абзацев. Версия 1.2 (этап 14): у паттерна group_id (группа взаимозаменяемых образцов), tone (светлый или тёмный фон с источником) и style_key (тон | семейство макета | photo или plain) — по ним планировщик выбирает стиль служебных слайдов единым внутри колоды и разным у вариантов.
    """

    schema_version: Literal["1.4"]
    template_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    template_hash: str
    """
    sha256 исходного файла; ключ кэша профиля вместе с analyzer.version
    """
    source_file: SourceFile
    analyzer: VersionRef
    created_at: AwareDatetime | None = None
    slide_size: SlideSize
    masters: list[Master] | None = None
    layouts: list[Layout]
    design_tokens: DesignTokens
    guides: list[Guide] | None = None
    fixed_elements: list[FixedElement]
    assets: list[AssetModel]
    patterns: list[Pattern]
    guidelines: list[Guideline] | None = None
    placeholder_markers: list[str] | None = None
    """
    Строки-заглушки, найденные в образцах шаблона: «Заголовок», «Текст», «Lorem ipsum», «ххх%», «Вставить фото». Используются при очистке и в проверке integrity.placeholder_text.
    """
    stats: Stats1
    llm_digest: str | None = None
    """
    Компактное текстовое описание профиля для промптов: палитра, шрифты, шкала, список паттернов с ролями и ёмкостью. Ориентир не более 2000 токенов.
    """
    warnings: list[Warning] | None = None
    sample_slides: list[SampleSlide] | None = None
    """
    Классификация каждого слайда шаблона: образец содержания, инструкция по оформлению, каталог ресурсов, пустой. Из паттернов исключаются все, кроме образцов.
    """
    dynamic_fields: list[DynamicField] | None = None
    """
    Поля, которые обновляются при смене порядка слайдов: номер слайда, дата, нумерация разделов
    """


class Op(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    op: Literal[
        "text.set",
        "text.replace",
        "text.insert_paragraph",
        "text.delete_paragraph",
        "text.reorder_paragraphs",
        "text.set_bullets",
        "text.set_notes",
        "style.size",
        "style.bold",
        "style.italic",
        "style.underline",
        "style.color",
        "style.align",
        "style.font",
        "style.spacing",
        "style.caps",
        "style.reset",
        "object.move",
        "object.resize",
        "object.align",
        "object.distribute",
        "object.z_order",
        "object.delete",
        "object.add_text",
        "object.add",
        "object.add_block",
        "object.duplicate",
        "picture.replace",
        "picture.insert",
        "picture.fit",
        "picture.recolor",
        "picture.pick_icon",
        "picture.set_qr",
        "background.solid",
        "background.image",
        "background.inherited",
        "table.set_cell",
        "table.add_row",
        "table.delete_row",
        "table.add_column",
        "table.delete_column",
        "table.sort",
        "table.reorder",
        "table.highlight",
        "table.layout",
        "table.from_dataset",
        "table.to_chart",
        "chart.set_values",
        "chart.add_series",
        "chart.delete_series",
        "chart.rename",
        "chart.set_type",
        "chart.colors",
        "chart.highlight",
        "chart.options",
        "chart.number_format",
        "chart.sort",
        "chart.from_image",
        "chart.from_dataset",
        "chart.from_text",
        "chart.to_table",
        "diagram.add",
        "diagram.delete",
        "diagram.reorder",
        "diagram.rename",
        "diagram.set_kind",
        "diagram.colors",
        "slide.rebuild",
        "slide.set_composition",
        "slide.fixed_toggle",
        "slide.hide",
        "slide.make_editable",
        "slide.placeholder_fill",
        "slide.placeholder_clear",
        "deck.add_slide",
        "deck.add_slides",
        "deck.duplicate",
        "deck.delete",
        "deck.move",
        "deck.split",
        "deck.merge",
        "deck.sections",
        "deck.agenda",
        "deck.replace_everywhere",
        "deck.translate",
        "deck.style_everywhere",
        "deck.recolor",
        "deck.logo",
        "deck.notes",
        "deck.update_facts",
        "deck.repair",
        "service.undo",
        "service.question",
        "service.refusal",
        "service.confirm",
    ]
    """
    семейство.имя
    """
    address: AddressModel
    source: Literal["snapshot", "package", "chat", "attachment", "template"]
    """
    откуда значения: текущий снимок (переформулировка, перестановка), факты пакета, слово человека, вложение, токены и ресурсы шаблона
    """
    confirm: bool
    """
    требует подтверждения кнопкой до исполнения: разрушающие и массовые операции
    """
    fact_refs: list[Id] | None = None
    """
    факты пакета или «чат», на которые опираются значения
    """
    args: dict[str, Any]


class Slide(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    slide_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    стабильный идентификатор из SlidePlan
    """
    index: int = Field(..., ge=0)
    """
    позиция в сохранённом файле
    """
    pptx_slide_part: str | None = None
    """
    например ppt/slides/slide3.xml
    """
    layout_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    pattern_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    source_slide_index: int | None = None
    """
    индекс образцового слайда шаблона, из которого клонирован
    """
    background: Background1 | None = None
    objects: list[Object]
    title: str | None = None
    """
    заголовок слайда из плана
    """
    notes: str | None = None
    source_slide_part: str | None = None
    """
    часть образца шаблона, из которой клонирован слайд
    """
    removed_object_ids: list[str] | None = None
    """
    объекты образца, удалённые при сборке: незаполненные карточки и слоты
    """
    overrides: list[Override] | None = None
    """
    ручные правки слайда из плана этой ревизии (эхо slides[].overrides)
    """
    overrides_dropped: list[OverridesDroppedItem] | None = None
    """
    правки, которые композер не применил: объект не найден, охрана адреса не совпала, операция не подходит виду объекта, ресурс отсутствует
    """


class SlideModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    index: int = Field(..., ge=1)
    """
    номер слайда в файле, с единицы
    """
    sld_id: int
    """
    p:sldId@id в presentation.xml этой ревизии
    """
    slide_id: str | None = Field(None, pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    слайд плана варианта; есть у ревизии варианта и у копии, сопоставленной с ней
    """
    role: str | None = None
    """
    роль слайда: из плана (роль паттерна) или по содержимому копии
    """
    layout: str
    """
    имя макета
    """
    title: str
    hidden: bool
    background: Background2
    notes: str
    """
    заметки докладчика
    """
    edited: bool | None = None
    """
    слайд копии отличается от исходной ревизии (ручные правки); нет поля — не сравнивали
    """
    objects: list[ObjectModel]
    """
    объекты слайда в порядке наложения снизу вверх; дети групп идут сразу за группой
    """


class SlideModel1(BaseModel):
    slide_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    order: int = Field(..., ge=1)
    role: str | None = None
    """
    роль из TemplateProfile.pattern.role
    """
    pattern_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    title: str
    """
    заголовок-вывод, а не название темы
    """
    key_message: str | None = None
    """
    пересказ слайда одним предложением; используется аудитом
    """
    blocks: list[BlockModel]
    notes: str | None = None
    source_refs: list[Id] | None = None
    fact_refs: list[Id] | None = None
    thesis_refs: list[Id] | None = None
    revision_note: str | None = None
    """
    что изменено исправлением относительно прошлой ревизии
    """
    overrides: list[Override] | None = None
    """
    ручные правки объектов слайда (этап 22): применяются композером по порядку после заполнения слотов и чистки; адресуют объекты ComposedDeck предыдущей ревизии
    """


class ChatOps(BaseModel):
    """
    Операции правки из чата (этап 36 серии «чат как редактор»): одна схема для плана варианта и офисной копии. У каждой операции адрес в снимке колоды (deck_snapshot) ревизии base, источник значений и признак «требует подтверждения». Операции исполняются по порядку; ошибка одной не откатывает уже применённые. overrides плана (common.override) — подмножество: text → text.set, style → style.*, geometry → object.move + object.resize, picture → picture.replace + picture.recolor, background → background.*, delete → object.delete, add_text → object.add_text; order и template_logo запроса slide_patch → deck.move, deck.logo. Номера слайдов, абзацев, строк, столбцов и шагов — с единицы.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: Literal["1.0"]
    base: Base
    """
    ревизия, к снимку которой относятся адреса
    """
    ops: list[Op] = Field(..., max_length=100)
    facts: list[ChatFact] | None = None
    """
    факты «чат»: значения, названные человеком (раздел 4.6)
    """


class ComposedDeck(BaseModel):
    """
    Описание фактически собранного PPTX одного варианта: объекты, вычисленные стили, геометрия, порядок слоёв, ресурсы, связи со слотами плана и исходными слайдами шаблона. Строится слоем вёрстки по сохранённому файлу и используется аудитом, подсветкой и HTML-экспортом. Версия 1.3 (этап 22): у слайда overrides — применённые ручные правки (эхо плана) и overrides_dropped — отброшенные с причиной; у объекта user_overrides и content_source user, geometry — форма фигуры (prstGeom) для холста редактора; у ресурса artifact — имя файла медиа в ревизии для интерфейса. Версия 1.2 (этап 8): composer и created_at, шрифты с подменой рендерера, статистика; у слайда заголовок, заметки, часть образца и удалённые объекты образца; у объекта content_source (содержимое из плана, оставленный текст образца, статика шаблона, построенный объект), вид слота и блока, fit из плана; у картинки режим вписывания и происхождение; у таблицы смещение строк и усечение; у диаграммы число категорий и способ построения.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: Literal["1.3"]
    deck_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    job_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    variant_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    revision: int = Field(..., ge=1)
    """
    Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
    """
    plan_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    template_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    pptx_hash: str
    """
    sha256 сохранённого PPTX этой ревизии
    """
    pptx_artifact: str | None = None
    """
    имя артефакта в манифесте задания
    """
    slide_size: SlideSize
    slides: list[Slide] = Field(..., min_length=1)
    assets: list[Asset] | None = None
    html_support: HtmlSupport
    """
    границы поддержки HTML-экспорта для этой колоды и причины резервного рендера
    """
    warnings: list[Warning] | None = None
    composer: VersionRef | None = None
    created_at: AwareDatetime | None = None
    fonts: list[Font2] | None = None
    """
    семейства шрифтов результата и подмена в рендерере (LibreOffice не использует встроенные шрифты)
    """
    stats: Stats | None = None
    template_logo: Literal["keep", "drop"] | None = "keep"
    """
    знак шаблона в этой колоде: drop — логотипы сняты с макетов, мастеров и слайдов
    """


class DeckSnapshot(BaseModel):
    """
    Снимок колоды для чата (этап 36): что на каждом слайде и где, одной формой для ревизии варианта и для офисной копии в ONLYOFFICE. Строится по байтам PPTX тем же обходом фигур, что и ComposedDeck (группы с путём, рамка плейсхолдера из макета, поворот); ComposedDeck и план ревизии добавляют идентификатор слайда плана, роль объекта по слоту и признак элемента шаблона. Адреса (номер слайда, sld_id, object_id) действительны только для ревизии снимка: ONLYOFFICE при сохранении перенумеровывает p:sldId и cNvPr id, сохраняя имена фигур. Номера слайдов, абзацев, строк и столбцов — с единицы.
    """

    model_config = ConfigDict(
        extra="forbid",
    )
    schema_version: Literal["1.0"]
    source: SourceModel
    slide_size: SlideSize
    slides: list[SlideModel]
    outline: list[OutlineEntry]
    """
    оглавление: по строке на слайд, в порядке файла
    """


class SlidePlan(BaseModel):
    """
    План одного варианта презентации: порядок слайдов, выбранные паттерны и содержание каждого слота. Создаётся слоем генерации, проверяется по схеме и по ёмкости слотов до вёрстки. Версия 1.3 (этап 22): у слайда overrides — ручные правки объектов из визуального редактора, которые композер применяет после заполнения слота; правка из чата заменяет слайд целиком и отбрасывает его overrides с предупреждением overrides_dropped. Версия 1.2 (этап 7): у блоков fit — результат измерения текста по метрикам шрифта после подстановки фактов (выбранный кегль, строки, действие лестницы ёмкости); у таблиц row_offset — часть большого набора данных на этом слайде; у slide_count target — целевое число слайдов варианта внутри диапазона; блок chart допускается в слоте image паттерна с ролью chart (картинка диаграммы в образце заменяется нативной диаграммой). Версия 1.1: ссылка на StoryPlan, покрытие обязательных тезисов, точное число или диапазон слайдов, данные для сопоставления вариантов. Соответствие kind содержимому блока проверяется схемой (allOf/if) и валидаторами.
    """

    schema_version: Literal["1.3"]
    plan_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    template_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    package_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    language: str
    variant: Variant1
    """
    Три варианта вёрстки одного контента различаются по одной заявленной оси. Ось и обоснование попадают в документацию и в интерфейс сравнения.
    """
    story: Story | None = None
    slides: list[SlideModel1] = Field(..., min_length=1)
    generation_meta: GenerationMeta
    warnings: list[Warning] | None = None
    story_id: str = Field(..., pattern="^[A-Za-z0-9_.:-]{1,80}$")
    """
    Стабильный идентификатор. Не содержит пробелов и путей.
    """
    slide_count: SlideCount3
    """
    Требование к числу слайдов, унаследованное из запроса: точное число или диапазон; план обязан ему соответствовать. target — целевое число слайдов варианта внутри диапазона (compact ближе к min, detailed к max)
    """
    coverage: Coverage1
    """
    Покрытие обязательных тезисов StoryPlan: заполняется планировщиком, проверяется валидатором
    """
    comparison: Comparison | None = None
    """
    Данные для сопоставления вариантов: последовательность паттернов и способы визуализации
    """
    template_logo: Literal["keep", "drop"] | None = "keep"
    """
    знак шаблона в колоде: keep — как в шаблоне, drop — снять логотипы со всех макетов, мастеров и слайдов (шаблон чужого подразделения без его логотипа)
    """


class Contracts(BaseModel):
    audit_report: AuditReport | None = None
    brief_extract: BriefExtract | None = None
    chat_ops: ChatOps | None = None
    composed_deck: ComposedDeck | None = None
    content_package: ContentPackage | None = None
    deck_snapshot: DeckSnapshot | None = None
    generation_request: GenerationRequest | None = None
    generation_result: GenerationResult | None = None
    job_status: JobStatus | None = None
    project: Project | None = None
    project_file: ProjectFile | None = None
    skill_manifest: SkillManifest | None = None
    slide_patch: SlidePatch | None = None
    slide_plan: SlidePlan | None = None
    story_plan: StoryPlan | None = None
    template_profile: TemplateProfile | None = None
