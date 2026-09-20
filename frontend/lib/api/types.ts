/* Сгенерировано scripts/gen-types.mjs из contracts/schemas. Не редактировать вручную. */

/**
 * Ручная правка одного объекта слайда или фона слайда из визуального редактора. Применяется композером после заполнения слота и чистки слайда, поэтому ревизия воспроизводится из плана. target.object_id — p:cNvPr@id объекта в ComposedDeck базовой ревизии (у объектов клона образца совпадает с образцом, у новых объектов детерминирован); source_object_id и slot_id — охрана адреса: при несовпадении правка отбрасывается с предупреждением, а не применяется к чужому объекту. Фон (op background) относится к слайду целиком, target не нужен. Операция delete убирает объект со слайда; add_text создаёт свою надпись, её target.object_id придуман редактором (объекта с таким идентификатором в базовой ревизии нет), а идентификатор готовой фигуры выводится из него детерминированно, поэтому пересборка ревизии повторяема.
 */
export type Override = {
  [k: string]: unknown;
} & {
  op: "text" | "style" | "geometry" | "picture" | "background" | "delete" | "add_text";
  target?: {
    object_id: string;
    source_object_id?: string;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    slot_id?: string;
  };
  /**
   * новый текст объекта (op text) или текст создаваемой надписи (op add_text): строки через \n; ссылки {fact:<id>} подставляются как в плане
   */
  text?: string;
  /**
   * оформление всех фрагментов объекта; передаются только изменяемые свойства; у add_text — оформление создаваемой надписи
   */
  style?: {
    font?: {
      family?: string;
      size_pt?: number;
      bold?: boolean;
      italic?: boolean;
      /**
       * Цвет в формате #RRGGBB
       */
      color?: string;
    };
    align?: "left" | "center" | "right" | "justify";
  };
  /**
   * новое положение и размер в долях слайда (как bbox ComposedDeck, с учётом групп); у add_text — рамка создаваемой надписи
   */
  geometry?: {
    bbox: Bbox1;
  };
  /**
   * замена картинки или иконки объекта p:pic; color — перекраска монохромной иконки шаблона
   */
  picture?: {
    source: AssetSource;
    fit?: "cover" | "contain";
    /**
     * Цвет в формате #RRGGBB
     */
    color?: string;
  };
  /**
   * фон слайда: сплошной цвет, картинка или наследование от макета
   */
  background?: {
    kind: "solid" | "image" | "inherited";
    /**
     * Цвет в формате #RRGGBB
     */
    color?: string;
    source?: AssetSource;
    fit?: "cover" | "contain";
  };
};
/**
 * Событие ленты чата: сообщение пользователя или карточка шага. Карточка хранит только идентификаторы и читает живое состояние
 */
export type Event = {
  [k: string]: unknown;
} & {
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  event_id: string;
  at: string;
  role: "user" | "assistant";
  kind:
    | "message"
    | "text"
    | "template_question"
    | "template_card"
    | "content_card"
    | "brief_card"
    | "job_card"
    | "audit_card"
    | "edit_card";
  text?: string;
  file_ids?: string[];
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  file_id?: string;
  /**
   * ответ на вопрос о PPTX: шаблон оформления, материал с содержанием или deck — готовая презентация как результат (этап 21)
   */
  resolved?: "template" | "material" | "deck";
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  template_id?: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  package_id?: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  job_id?: string;
  understood?: string[];
  missing_purpose?: boolean;
  /**
   * для brief_card: чем извлечён бриф из сообщения — моделью или детерминированными правилами (резерв)
   */
  brief_source?: "model" | "heuristic";
  /**
   * для сообщения пользователя: слайд, к которому обращена просьба (чип в поле ввода)
   */
  slide_ref?: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    job_id: string;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    variant_id: string;
    /**
     * Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
     */
    revision: number;
    slide_index: number;
  };
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  variant_id?: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  edit_job_id?: string;
  slide_index?: number;
};
/**
 * Содержание одного слота. Ровно одно из полей содержания должно соответствовать kind слота; исключение — блок chart в слоте image паттерна с ролью chart.
 */
export type Block = {
  [k: string]: unknown;
} & {
  [k: string]: unknown;
} & {
  [k: string]: unknown;
} & {
  [k: string]: unknown;
} & {
  [k: string]: unknown;
} & {
  [k: string]: unknown;
} & {
  [k: string]: unknown;
} & {
  [k: string]: unknown;
} & {
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  slot_id: string;
  kind:
    | "title"
    | "subtitle"
    | "body"
    | "bullets"
    | "number"
    | "label"
    | "caption"
    | "date"
    | "name"
    | "position"
    | "image"
    | "icon"
    | "table"
    | "chart"
    | "diagram"
    | "qr"
    | "code";
  /**
   * может содержать {fact:<fact_id>}; композер подставляет значение из реестра
   */
  text?: string;
  items?: {
    text: string;
    icon?: Icon;
    fact_refs?: string[];
  }[];
  number?: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    fact_id: string;
    /**
     * например «{value} %» или «{value} млн ₽»
     */
    format?: string;
  };
  table?: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    dataset_id: string;
    columns?: string[];
    max_rows?: number;
    highlight_row?: number;
    /**
     * с какой строки набора данных начинается таблица этого слайда; большие наборы планировщик делит между слайдами
     */
    row_offset?: number;
  };
  /**
   * Нативная диаграмма PowerPoint; стиль берётся из палитры и правил шаблона
   */
  chart?: {
    type: "column" | "bar" | "stacked_column" | "line" | "area" | "pie" | "doughnut" | "scatter";
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    dataset_id: string;
    category_column?: string;
    /**
     * @maxItems 5
     */
    series:
      | []
      | [string]
      | [string, string]
      | [string, string, string]
      | [string, string, string, string]
      | [string, string, string, string, string];
    title?: string;
    units?: string;
    show_legend?: boolean;
    show_axis_labels?: boolean;
    show_data_labels?: boolean;
  };
  image?: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    asset_id?: string;
    /**
     * задача со звёздочкой: генерация изображения моделью text-to-image
     */
    generate?: {
      prompt: string;
      negative_prompt?: string;
      style?: string;
    };
    fit?: "cover" | "contain";
    alt?: string;
  };
  icon?: Icon;
  /**
   * Схема из нативных фигур: замена SmartArt
   */
  diagram?: {
    kind: "process" | "cycle" | "pyramid" | "hierarchy" | "matrix" | "funnel" | "timeline" | "venn";
    items: {
      text: string;
      sub?: string;
      icon?: Icon;
    }[];
    direction?: "horizontal" | "vertical";
  };
  source_refs?: string[];
  fact_refs?: string[];
  /**
   * Измерение текста блока по метрикам шрифта после подстановки фактов: выбранный кегль и что сделала лестница ёмкости
   */
  fit?: {
    /**
     * кегль, с которым текст помещается; равен кеглю слота, если уменьшать не пришлось
     */
    size_pt: number;
    slot_size_pt?: number;
    lines: number;
    max_lines: number;
    chars?: number;
    /**
     * overflow — текст не помещается и после лестницы; такой план не выдаётся при точном числе слайдов
     */
    action: "as_is" | "pattern_swap" | "font_step" | "shortened" | "split" | "overflow";
    note?: string;
  };
};

export interface Contracts {
  audit_report?: AuditReport;
  brief_extract?: BriefExtract;
  composed_deck?: ComposedDeck;
  content_package?: ContentPackage;
  generation_request?: GenerationRequest;
  generation_result?: GenerationResult;
  job_status?: JobStatus;
  project?: Project;
  project_file?: ProjectFile;
  skill_manifest?: SkillManifest;
  slide_patch?: SlidePatch;
  slide_plan?: SlidePlan;
  story_plan?: StoryPlan;
  template_profile?: TemplateProfile;
}
/**
 * Отчёт аудита одной ревизии одного варианта. Реестр проверок повторяет Приложение 1 ТЗ и расширяется своими проверками. Версия 1.1: результат каждой проверки по области passed/failed/not_applicable/not_checked с причиной; серьёзность отдельно от способа; ревизия; входы контекстных проверок; покрытие и зависимые повторные проверки.
 */
export interface AuditReport {
  schema_version: "1.1";
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  report_id: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  job_id: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  variant_id: string;
  created_at: string;
  deck: {
    pptx_artifact: string;
    slide_count: number;
    renderer?: VersionRef;
    thumbnail_width_px?: number;
    pptx_hash?: string;
    composed_deck_artifact?: string;
    fonts?: {
      requested?: string;
      actual?: string;
      file?: string;
    }[];
  };
  /**
   * Что запускалось. Список целиком, включая проверки без находок: из него собирается AUDIT.md.
   */
  checks: {
    check_id: string;
    name: string;
    category: "layout" | "template" | "density" | "integrity" | "content";
    kind: "deterministic" | "contextual";
    version: string;
    scope: "slide" | "deck";
    origin?: "appendix1" | "own";
    implemented: boolean;
    threshold?: {
      [k: string]: unknown;
    };
    /**
     * правило применимости: роли паттернов или условия, при которых проверка не применяется
     */
    applicability?: string;
    /**
     * входы проверки; для контекстных показывает, что модель получила помимо картинки
     */
    inputs?: (
      | "xml"
      | "composed_deck"
      | "render"
      | "font_metrics"
      | "template_profile"
      | "story_plan"
      | "content_package"
      | "neighbor_slides"
      | "whole_deck_text"
    )[];
  }[];
  issues: Issue[];
  /**
   * Ответы модели на 11 вопросов валидации контента по картинке слайда
   */
  contextual_answers?: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    slide_id: string;
    slide_index?: number;
    question_id: number;
    question?: string;
    answer: "yes" | "no" | "unsure";
    confidence?: number;
    explanation?: string;
    model?: ModelRef;
    prompt?: VersionRef;
    inputs?: string[];
    outcome?: "passed" | "failed" | "not_applicable" | "not_checked";
  }[];
  summary: {
    issues_total: number;
    by_severity: {
      [k: string]: number;
    };
    by_category?: {
      [k: string]: number;
    };
    by_kind?: {
      [k: string]: number;
    };
    slides_with_issues?: number;
    score?: number;
  };
  metrics?: {
    duration_ms?: number;
    llm_calls?: number;
    prompt_tokens?: number;
    completion_tokens?: number;
  };
  /**
   * Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
   */
  revision: number;
  /**
   * Результат каждой проверки для каждой области (слайд или колода), включая пройденные и неприменимые
   */
  results: {
    check_id: string;
    scope: "slide" | "deck";
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    slide_id?: string;
    slide_index?: number;
    outcome: "passed" | "failed" | "not_applicable" | "not_checked";
    /**
     * обязательна для not_applicable и not_checked
     */
    reason?: string;
    issue_ids?: string[];
    duration_ms?: number;
  }[];
  coverage: {
    /**
     * все обязательные проверки выполнены
     */
    complete: boolean;
    checked?: number;
    not_checked?: number;
    not_applicable?: number;
    /**
     * например: vlm_unavailable, render_failed
     */
    missing_inputs?: string[];
  };
  /**
   * Что повторно проверено после исправления и почему
   */
  rechecked_after_repair?: {
    /**
     * Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
     */
    base_revision?: number;
    changed_slide_ids?: string[];
    /**
     * соседи и слайды, затронутые порядком или покрытием фактов
     */
    dependent_slide_ids?: string[];
    deck_checks_rerun?: string[];
  };
}
/**
 * Ссылка на версионируемый компонент: скилл, промпт, анализатор, рендерер
 */
export interface VersionRef {
  name: string;
  version: string;
}
export interface Issue {
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  issue_id: string;
  check_id: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  slide_id?: string;
  slide_index: number;
  /**
   * blocking мешает статусу succeeded
   */
  severity: "blocking" | "error" | "warning" | "info";
  /**
   * способ проверки; серьёзность задаётся отдельно полем severity
   */
  kind: "deterministic" | "contextual";
  /**
   * понятная пользователю формулировка
   */
  message: string;
  bbox?: Bbox;
  element_ids?: string[];
  /**
   * измеренное значение и порог: например measured 7 буллетов при пороге 6
   */
  evidence?: {
    measured?: unknown;
    threshold?: unknown;
    details?: string;
  };
  fix: {
    available: boolean;
    strategy?:
      | "shrink_text"
      | "rewrite_shorter"
      | "split_slide"
      | "change_pattern"
      | "move_element"
      | "resize_element"
      | "recolor"
      | "replace_font"
      | "remove_placeholder"
      | "regenerate_text"
      | "regenerate_image"
      | "add_labels"
      | "remove_slide"
      | "none";
    description?: string;
    cost?: "cheap" | "llm" | "rerender";
    affects?: ("slide" | "neighbors" | "deck_order" | "fact_coverage" | "all_variants")[];
  };
  status: "open" | "selected" | "fixed" | "ignored" | "unfixable";
  /**
   * Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
   */
  revision?: number;
  /**
   * template: дефект присутствует в исходном шаблоне
   */
  origin?: "generated" | "template";
}
/**
 * область для подсветки на миниатюре
 */
export interface Bbox {
  x: number;
  y: number;
  width: number;
  height: number;
}
/**
 * Модель, использованная на этапе. Заполняется из конфигурации моделей; hf_url обязателен для MODELS.md.
 */
export interface ModelRef {
  role: "llm" | "vlm" | "text_to_image" | "embedding";
  name: string;
  provider?: string;
  hf_url?: string;
  params_b?: number;
  /**
   * активные параметры для MoE
   */
  active_params_b?: number;
  license?: string;
  reasoning_mode?: string;
}
/**
 * Ответ POST /api/brief: бриф и настройки, извлечённые из свободного сообщения чата. Запрос описан в $defs/request. Поля, которых нет в тексте, не заполняются; understood перечисляет найденные. Ответ — предложение для подтверждения пользователем, а не решение. Версия 1.2.
 */
export interface BriefExtract {
  schema_version: "1.2";
  brief: {
    purpose?: "feature" | "product" | "project" | "initiative" | "report" | "other";
    title?: string;
    audience?: string;
    goal?: string;
    language?: string;
    tone?: string;
    must_include?: string[];
    avoid?: string[];
  };
  slide_count?: {
    exact?: number;
    min?: number;
    max?: number;
  };
  variants?: ("compact" | "balanced" | "detailed")[];
  /**
   * какие поля действительно найдены в тексте: purpose, title, audience, goal, tone, language, must_include, avoid, slide_count, variants
   */
  understood: string[];
  /**
   * generate: явная команда запустить генерацию; edit: правка готовых слайдов; none: описание задачи или ничего
   */
  intent: "generate" | "edit" | "none";
  /**
   * model: извлечено моделью; heuristic: детерминированные правила без модели
   */
  source: "model" | "heuristic";
  model?: ModelRef;
}
/**
 * Описание фактически собранного PPTX одного варианта: объекты, вычисленные стили, геометрия, порядок слоёв, ресурсы, связи со слотами плана и исходными слайдами шаблона. Строится слоем вёрстки по сохранённому файлу и используется аудитом, подсветкой и HTML-экспортом. Версия 1.3 (этап 22): у слайда overrides — применённые ручные правки (эхо плана) и overrides_dropped — отброшенные с причиной; у объекта user_overrides и content_source user, geometry — форма фигуры (prstGeom) для холста редактора; у ресурса artifact — имя файла медиа в ревизии для интерфейса. Версия 1.2 (этап 8): composer и created_at, шрифты с подменой рендерера, статистика; у слайда заголовок, заметки, часть образца и удалённые объекты образца; у объекта content_source (содержимое из плана, оставленный текст образца, статика шаблона, построенный объект), вид слота и блока, fit из плана; у картинки режим вписывания и происхождение; у таблицы смещение строк и усечение; у диаграммы число категорий и способ построения.
 */
export interface ComposedDeck {
  schema_version: "1.3";
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  deck_id: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  job_id: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  variant_id: string;
  /**
   * Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
   */
  revision: number;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  plan_id: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  template_id: string;
  /**
   * sha256 сохранённого PPTX этой ревизии
   */
  pptx_hash: string;
  /**
   * имя артефакта в манифесте задания
   */
  pptx_artifact?: string;
  slide_size: SlideSize;
  /**
   * @minItems 1
   */
  slides: [Slide, ...Slide[]];
  assets?: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    asset_id: string;
    media_path: string;
    sha256: string;
    content_type?: string;
    origin?: "template" | "content" | "generated" | "icon_library";
    /**
     * неизменяемый ресурс, разделяемый с исходным пакетом
     */
    shared_with_template?: boolean;
    /**
     * имя артефакта ревизии с байтами ресурса (<variant>/r<N>/media/<file>), если медиа выложено рядом с ревизией
     */
    artifact?: string;
  }[];
  /**
   * границы поддержки HTML-экспорта для этой колоды и причины резервного рендера
   */
  html_support: {
    full_native: boolean;
    supported_kinds?: (
      "text" | "picture" | "table" | "chart" | "shape" | "connector" | "group" | "placeholder_empty" | "other"
    )[];
    fallback_elements: {
      /**
       * Стабильный идентификатор. Не содержит пробелов и путей.
       */
      slide_id: string;
      object_id: string;
      reason: string;
      fallback?: "raster_from_render" | "omitted";
    }[];
  };
  warnings?: Warning[];
  composer?: VersionRef;
  created_at?: string;
  /**
   * семейства шрифтов результата и подмена в рендерере (LibreOffice не использует встроенные шрифты)
   */
  fonts?: {
    family: string;
    available_in_renderer?: boolean;
    fallback?: string;
    embedded?: boolean;
  }[];
  stats?: {
    slides?: number;
    objects?: number;
    text_objects?: number;
    pictures?: number;
    tables?: number;
    charts?: number;
    diagrams?: number;
    removed_objects?: number;
    layouts_kept?: number;
    layouts_removed?: number;
    file_size_bytes?: number;
  };
  /**
   * знак шаблона в этой колоде: drop — логотипы сняты с макетов, мастеров и слайдов
   */
  template_logo?: "keep" | "drop";
}
/**
 * Размер слайда в EMU. В датасете встречаются 12192000×6858000 и 9144000×5143500, поэтому кегли сравниваются только внутри одного шаблона.
 */
export interface SlideSize {
  width_emu: number;
  height_emu: number;
  aspect_ratio: number;
}
export interface Slide {
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  slide_id: string;
  /**
   * позиция в сохранённом файле
   */
  index: number;
  /**
   * например ppt/slides/slide3.xml
   */
  pptx_slide_part?: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  layout_id: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  pattern_id?: string;
  /**
   * индекс образцового слайда шаблона, из которого клонирован
   */
  source_slide_index?: number;
  background?: {
    kind?: "solid" | "gradient" | "image" | "inherited";
    /**
     * Цвет в формате #RRGGBB
     */
    color?: string;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    asset_id?: string;
  };
  objects: Object[];
  /**
   * заголовок слайда из плана
   */
  title?: string;
  notes?: string;
  /**
   * часть образца шаблона, из которой клонирован слайд
   */
  source_slide_part?: string;
  /**
   * объекты образца, удалённые при сборке: незаполненные карточки и слоты
   */
  removed_object_ids?: string[];
  /**
   * ручные правки слайда из плана этой ревизии (эхо slides[].overrides)
   */
  overrides?: Override[];
  /**
   * правки, которые композер не применил: объект не найден, охрана адреса не совпала, операция не подходит виду объекта, ресурс отсутствует
   */
  overrides_dropped?: {
    op: string;
    target?: {
      object_id?: string;
      source_object_id?: string;
      /**
       * Стабильный идентификатор. Не содержит пробелов и путей.
       */
      slot_id?: string;
    };
    code: string;
    message: string;
  }[];
}
export interface Object {
  /**
   * p:cNvPr@id внутри слайда
   */
  object_id: string;
  name?: string;
  kind: "text" | "picture" | "table" | "chart" | "shape" | "connector" | "group" | "placeholder_empty" | "other";
  bbox: Bbox1;
  rotation_deg?: number;
  /**
   * форма фигуры (a:prstGeom@prst): rect, roundRect, ellipse…; отсутствует у произвольной геометрии
   */
  geometry?: string;
  /**
   * скругление углов (a:prstGeom/a:avLst «adj») долей от меньшей стороны фигуры: в PowerPoint радиус считается от неё, а не от каждой стороны отдельно
   */
  geometry_adjust?: number;
  z_order: number;
  /**
   * идентификаторы групп от внешней к внутренней
   */
  group_path?: string[];
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  slot_id?: string;
  /**
   * объект образцового слайда шаблона
   */
  source_object_id?: string;
  role?: "content" | "fixed" | "decoration" | "background";
  text?: {
    plain?: string;
    paragraphs?: {
      text?: string;
      level?: number;
      bullet?: boolean;
      align?: "left" | "center" | "right" | "justify";
      style?: ComputedTextStyle;
    }[];
    computed_style?: ComputedTextStyle;
    insets?: {
      left?: number;
      top?: number;
      right?: number;
      bottom?: number;
    };
    autofit?: "none" | "shrink" | "resize_shape";
    /**
     * вертикальная привязка текста в рамке (a:bodyPr@anchor): по ней текст стоит там же, где в PowerPoint
     */
    anchor?: "top" | "middle" | "bottom";
    fact_refs?: string[];
  };
  picture?: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    asset_id?: string;
    crop?: {
      left?: number;
      top?: number;
      right?: number;
      bottom?: number;
    };
    natural_width_px?: number;
    natural_height_px?: number;
    fit?: "cover" | "contain" | "as_is";
    origin?: "template" | "content" | "generated" | "icon_library";
    recolored?: boolean;
  };
  table?: {
    rows?: number;
    cols?: number;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    dataset_id?: string;
    header_row?: boolean;
    row_offset?: number;
    /**
     * набор данных не поместился целиком на этот слайд
     */
    truncated?: boolean;
  };
  chart?: {
    chart_part?: string;
    type?: string;
    series_count?: number;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    dataset_id?: string;
    has_legend?: boolean;
    has_axis_titles?: boolean;
    has_data_labels?: boolean;
    units?: string;
    categories_count?: number;
    /**
     * replaced — данные подставлены в диаграмму образца; rebuilt — образец заменён новой; added — построена на месте картинки или плейсхолдера
     */
    built?: "replaced" | "rebuilt" | "added";
  };
  fill?: {
    kind?: "none" | "solid" | "gradient" | "image" | "inherited";
    /**
     * Цвет в формате #RRGGBB
     */
    color?: string;
    source?: StyleSource;
  };
  line?: {
    /**
     * Цвет в формате #RRGGBB
     */
    color?: string;
    width_pt?: number;
  };
  /**
   * plan — содержимое из блока плана; sample — намеренно оставленный текст или картинка образца (крошечный слот, незаполненный слот изображения), аудит не считает его заглушкой; template — статика образца, макета или мастера; generated — объект, построенный композером (диаграмма, таблица, схема); user — содержимое заменено ручной правкой из визуального редактора
   */
  content_source?: "plan" | "sample" | "template" | "generated" | "user";
  /**
   * ручные правки, применённые к этому объекту в этой ревизии
   */
  user_overrides?: Override[];
  /**
   * вид слота профиля, из которого пришёл объект
   */
  slot_kind?: string;
  /**
   * вид блока плана; отличается от slot_kind у диаграммы в слоте image
   */
  block_kind?: string;
  /**
   * измерение из плана (blocks[].fit): выбранный кегль и действие лестницы ёмкости
   */
  fit?: {
    size_pt?: number;
    slot_size_pt?: number;
    lines?: number;
    max_lines?: number;
    action?: string;
  };
  /**
   * схема из фигур: группа-контейнер и её узлы (не SmartArt)
   */
  diagram?: {
    kind?: string;
    node_ids?: string[];
  };
}
/**
 * Прямоугольник в долях ширины и высоты слайда; начало координат в левом верхнем углу. Значения вне 0..1 допустимы: так описываются элементы, вышедшие за слайд.
 */
export interface Bbox1 {
  x: number;
  y: number;
  width: number;
  height: number;
}
/**
 * Вычисленный стиль текста после разрешения наследования, с источником каждого свойства
 */
export interface ComputedTextStyle {
  font?: FontSpec;
  font_source?: StyleSource;
  color_source?: StyleSource;
  size_source?: StyleSource;
  space_before_pt?: number;
  space_after_pt?: number;
  indent_emu?: number;
  bullet?: {
    kind?: "none" | "char" | "number" | "picture";
    char?: string;
  };
}
/**
 * Шрифт текстовой области. size_pt всегда в пунктах и всегда вместе с размером слайда в профиле.
 */
export interface FontSpec {
  family?: string;
  size_pt?: number;
  bold?: boolean;
  italic?: boolean;
  /**
   * Цвет в формате #RRGGBB
   */
  color?: string;
  /**
   * множитель межстрочного интервала
   */
  line_spacing?: number;
  all_caps?: boolean;
}
/**
 * Откуда унаследовано вычисленное свойство стиля
 */
export interface StyleSource {
  level:
    | "theme"
    | "master"
    | "layout"
    | "placeholder"
    | "shape"
    | "paragraph"
    | "run"
    | "table_style"
    | "chart_style"
    | "default";
  /**
   * часть пакета, например ppt/slideMasters/slideMaster1.xml
   */
  part?: string;
  /**
   * ссылка на цвет или шрифт темы, например accent1 или +mj-lt
   */
  theme_ref?: string;
  /**
   * модификаторы цвета темы: lumMod, lumOff, alpha и т. п.
   */
  modifiers?: string[];
}
/**
 * Источник картинки для ручной правки: ресурс шаблона (asset_id из TemplateProfile.assets), ресурс контент-пакета (asset_id из ContentPackage.assets) или загруженный файл проекта (file_id; sha256 дописывает конвейер, name — для подписи в интерфейсе)
 */
export interface AssetSource {
  kind: "template" | "package" | "file";
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  asset_id?: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  file_id?: string;
  sha256?: string;
  name?: string;
}
export interface Warning {
  code: string;
  message: string;
  slide_index?: number;
}
/**
 * Результат слоя импорта содержания. Два входа: контент-пакет (файлы) и краткий бриф с назначением. Факты и наборы данных извлекаются детерминированно до вызова модели. Версия 1.3 (этап 6): import_meta с версиями парсеров, ключом кэша и вызовами модели; у источников parser и число единиц (страниц, листов, слайдов); у блоков source_location и caption; у ресурсов sha256 и mime; у наборов данных source_location, total_rows и truncated. Версия 1.2: источник ссылается на файл проекта (file_id), вид pptx для материалов-презентаций, неполный бриф дополняется умолчаниями с предупреждением brief_incomplete. Версия 1.1: контекст факта (показатель, период, субъект, единица, исходный фрагмент или ячейка), производные показатели с формулой, отметка неопределённости.
 */
export interface ContentPackage {
  schema_version: "1.3";
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  package_id: string;
  /**
   * package: содержание дано; brief: структуру и текст генерирует сервис; mixed: бриф плюс материалы
   */
  mode: "package" | "brief" | "mixed";
  created_at?: string;
  brief: {
    purpose: "feature" | "product" | "project" | "initiative" | "report" | "other";
    title: string;
    audience?: string;
    /**
     * чего должна добиться презентация: одобрение, информирование, продажа
     */
    goal?: string;
    language: string;
    tone?: string;
    slide_count?: {
      min?: number;
      max?: number;
    };
    must_include?: string[];
    avoid?: string[];
    notes?: string;
  };
  sources: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    source_id: string;
    kind: "text" | "markdown" | "docx" | "pdf" | "xlsx" | "csv" | "json" | "image" | "pptx" | "url" | "user_input";
    name: string;
    sha256?: string;
    size_bytes?: number;
    extracted?: boolean;
    warnings?: Warning[];
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    file_id?: string;
    parser?: VersionRef1;
    /**
     * сколько единиц содержания разобрано: страниц, листов, слайдов, таблиц, изображений
     */
    units?: {
      pages?: number;
      sheets?: number;
      slides?: number;
      tables?: number;
      images?: number;
      chars?: number;
    };
  }[];
  /**
   * Содержание в порядке исходников. Планировщик ссылается на block_id, а не копирует текст без ссылки.
   */
  blocks: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    block_id: string;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    source_id: string;
    order: number;
    kind: "heading" | "paragraph" | "bullets" | "table" | "figure" | "quote" | "kpi" | "code";
    /**
     * уровень заголовка
     */
    level?: number;
    text?: string;
    items?: string[];
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    dataset_id?: string;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    asset_id?: string;
    importance?: "must" | "should" | "could";
    tags?: string[];
    /**
     * Место блока в источнике
     */
    source_location?: {
      page?: number;
      sheet?: string;
      /**
       * номер слайда материала-презентации
       */
      slide?: number;
      /**
       * например A1:C4
       */
      cell_range?: string;
      char_offset?: number;
      /**
       * текст из заметок докладчика
       */
      notes?: boolean;
    };
    /**
     * подпись таблицы или рисунка из источника
     */
    caption?: string;
  }[];
  /**
   * Реестр фактов. В тексте планов факты подставляются через {fact:<fact_id>}; модель не переписывает значения.
   */
  facts: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    fact_id: string;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    source_id: string;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    block_id?: string;
    /**
     * как написано в источнике: «12,5 млн ₽»
     */
    raw: string;
    kind: "number" | "percent" | "money" | "date" | "range" | "ratio" | "name" | "other";
    value?: number | string;
    unit?: string;
    /**
     * что измеряет факт: «выручка за 2025 год»
     */
    label?: string;
    must_keep?: boolean;
    /**
     * Что именно измеряет факт. «Рост выручки на 25 %» и «рост прибыли на 25 %» различаются контекстом, а не значением.
     */
    context?: {
      /**
       * показатель: выручка, DAU, доля рынка
       */
      metric?: string;
      /**
       * период: 2025 год, 3 квартал, май
       */
      period?: string;
      /**
       * субъект: компания, продукт, сегмент
       */
      subject?: string;
      unit?: string;
      /**
       * с чем сравнение: год к году, план
       */
      comparison?: string;
    };
    /**
     * Точное место в источнике
     */
    source_location?: {
      /**
       * исходный фрагмент текста вокруг факта
       */
      fragment?: string;
      page?: number;
      sheet?: string;
      /**
       * например B12
       */
      cell?: string;
      char_offset?: number;
    };
    /**
     * Производный показатель, вычисленный кодом из других фактов
     */
    derived?: {
      /**
       * например (f2 - f1) / f1 * 100
       */
      formula: string;
      inputs: string[];
    };
    uncertainty?: {
      level?: "confirmed" | "inferred" | "assumed" | "missing";
      note?: string;
      extracted_by?: "regex" | "parser" | "model" | "user";
      confidence?: number;
    };
  }[];
  assets: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    asset_id: string;
    kind: "image" | "logo" | "screenshot" | "chart_image" | "diagram_image" | "photo";
    /**
     * путь в рабочем каталоге задания
     */
    path: string;
    width_px?: number;
    height_px?: number;
    caption?: string;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    source_id?: string;
    sha256?: string;
    mime?: string;
  }[];
  /**
   * Табличные данные для таблиц и графиков
   */
  datasets: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    dataset_id: string;
    title?: string;
    columns: {
      name: string;
      type: "string" | "number" | "date" | "percent" | "money";
      unit?: string;
    }[];
    rows: (string | number | null)[][];
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    source_id?: string;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    block_id?: string;
    source_location?: {
      page?: number;
      sheet?: string;
      slide?: number;
      cell_range?: string;
    };
    /**
     * строк в источнике до усечения
     */
    total_rows?: number;
    /**
     * rows усечены до предела импорта; total_rows хранит полное число
     */
    truncated?: boolean;
  }[];
  warnings?: Warning[];
  /**
   * Данные, которых не хватает для брифа: не выдумываются, а помечаются
   */
  missing_data?: {
    what: string;
    why_needed?: string;
    thesis_hint?: string;
  }[];
  /**
   * Как был собран пакет: версии импортёра и парсеров, ключ кэша, попадания в кэш разбора файлов, вызовы модели для уточнения контекста фактов
   */
  import_meta?: {
    importer: VersionRef;
    /**
     * версия парсера по формату: docx, xlsx, csv, pdf, markdown, text, pptx, image
     */
    parsers?: {
      [k: string]: string;
    };
    /**
     * sha256 от sha256 файлов в их порядке, параметров разбора и версий парсеров; бриф в ключ не входит — его смена не перечитывает файлы
     */
    import_key: string;
    cache?: {
      files_hit?: number;
      files_missed?: number;
    };
    skills?: VersionRef[];
    prompts?: VersionRef[];
    models?: ModelRef[];
    /**
     * запросов к модели для уточнения контекста фактов
     */
    model_calls?: number;
    duration_ms?: number;
    created_at?: string;
  };
}
/**
 * Ссылка на версионируемый компонент: скилл, промпт, анализатор, рендерер
 */
export interface VersionRef1 {
  name: string;
  version: string;
}
/**
 * Тело POST /api/generations и вход CLI-команды generate. Версия 1.2: вариант original — загруженная презентация как готовый результат (шаблон и материал — один и тот же файл; план строится из профиля без модели, слайды и тексты сохраняются как есть; сочетается только сам с собой). Приоритет: явные настройки запроса → бриф ContentPackage → умолчания config/app.yaml. Пути файлов от клиента не принимаются: только идентификаторы.
 */
export interface GenerationRequest {
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  template_id: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  package_id: string;
  settings?: {
    /**
     * точное число или диапазон; при обоих заданных exact имеет приоритет; min <= max проверяется валидатором
     */
    slide_count?: {
      exact?: number;
      min?: number;
      max?: number;
    };
    language?: string;
    /**
     * варианты вёрстки; original — исходная презентация как есть, только один в списке (проверяется валидатором)
     *
     * @minItems 1
     * @maxItems 3
     */
    variants?:
      | ["compact" | "balanced" | "detailed" | "original"]
      | ["compact" | "balanced" | "detailed" | "original", "compact" | "balanced" | "detailed" | "original"]
      | [
          "compact" | "balanced" | "detailed" | "original",
          "compact" | "balanced" | "detailed" | "original",
          "compact" | "balanced" | "detailed" | "original"
        ];
    /**
     * генерация новых изображений моделью; доступно только при включённой возможности сервиса
     */
    generate_images?: boolean;
    /**
     * отключение даёт неполный аудит и статус needs_review
     */
    run_contextual_audit?: boolean;
    /**
     * передаётся провайдеру, побитовая воспроизводимость не гарантируется
     */
    seed?: number;
    /**
     * явная перегенерация: кэш ответов модели не используется
     */
    force_regenerate?: boolean;
  };
  schema_version: "1.2";
  /**
   * повтор запроса с тем же ключом возвращает то же задание
   */
  idempotency_key?: string;
}
/**
 * Результат задания генерации. Отдаётся по ссылке из JobStatus и напрямую GET /api/generations/{id}; пока задание идёт, поля вариантов заполняются по мере готовности. Версия 1.3 (этап 22): у правок edits[] origin — источник (chat: инструкция модели, editor: ручные правки визуального редактора) и summary — сводка правок. Версия 1.1: ревизии, режим исполнения слоёв, частичные результаты, полнота аудита, версии рендерера и шрифтов, ожидание очереди и квоты, повторы, кэши, время первого и всех готовых вариантов.
 */
export interface GenerationResult {
  schema_version: "1.3";
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  job_id: string;
  /**
   * succeeded: обязательные проверки завершены и блокирующих проблем нет; needs_review: результат пригоден к просмотру, но есть находки или неполный аудит
   */
  status: "queued" | "running" | "succeeded" | "needs_review" | "failed" | "canceled";
  stage:
    | "queued"
    | "analyze"
    | "import"
    | "story"
    | "plan"
    | "compose"
    | "export"
    | "audit"
    | "repair"
    | "finalize"
    | "done";
  progress?: Progress;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  template_id: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  package_id: string;
  request?: GenerationRequest;
  created_at: string;
  finished_at?: string;
  variants: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    variant_id: string;
    axis: string;
    value: string;
    rationale?: string;
    status: "pending" | "running" | "ready" | "needs_review" | "failed";
    slide_count?: number;
    plan_artifact?: string;
    audit_artifact?: string;
    audit_summary?: {
      [k: string]: unknown;
    };
    artifacts?: {
      pptx?: string;
      pdf?: string;
      html?: string;
      thumbnails?: {
        slide_index: number;
        name: string;
        width_px?: number;
        height_px?: number;
      }[];
    };
    /**
     * Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
     */
    revision: number;
    /**
     * история ревизий варианта
     */
    revisions?: {
      /**
       * Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
       */
      revision: number;
      created_at: string;
      /**
       * Стабильный идентификатор. Не содержит пробелов и путей.
       */
      repair_job_id?: string;
      changed_slide_ids?: string[];
      pptx_hash?: string;
      artifacts_prefix?: string;
    }[];
    audit?: {
      status?: "pending" | "running" | "complete" | "partial" | "failed" | "skipped";
      coverage_complete?: boolean;
      issues_total?: number;
      blocking?: number;
      report_artifact?: string;
    };
    error?: Error;
    composed_deck_artifact?: string;
    /**
     * момент готовности файлов варианта
     */
    ready_at?: string;
    audited_at?: string;
    stages?: StageTiming[];
  }[];
  /**
   * Единственный источник разрешённых имён для GET /api/jobs/{id}/artifacts/{name}
   */
  artifacts_manifest?: {
    [k: string]: {
      content_type: string;
      size_bytes: number;
      sha256?: string;
    };
  };
  metrics: {
    stages?: StageTiming[];
    llm_calls?: {
      stage: string;
      variant_id?: string;
      slide_ids?: string[];
      attempt: number;
      model: string;
      prompt?: VersionRef;
      prompt_tokens?: number;
      completion_tokens?: number;
      latency_ms?: number;
      cache_hit?: boolean;
      ok?: boolean;
      quota_wait_ms?: number;
      reasoning_tokens?: number;
      error_code?: string;
    }[];
    totals: {
      duration_ms?: number;
      llm_calls?: number;
      prompt_tokens?: number;
      completion_tokens?: number;
      peak_memory_mb?: number;
    };
    cost_estimate?: {
      known: boolean;
      currency?: string;
      amount?: number;
    };
    queue_wait_ms?: number;
    /**
     * суммарное ожидание лимитера провайдера
     */
    quota_wait_ms?: number;
    retries?: number;
    cache?: {
      llm_hits?: number;
      llm_misses?: number;
      profile_hit?: boolean;
      story_hit?: boolean;
      render_hits?: number;
    };
    /**
     * ключевые моменты от принятия задания
     */
    timeline?: {
      accepted_at?: string;
      first_file_ready_ms?: number;
      first_variant_audited_ms?: number;
      all_variants_ready_ms?: number;
      all_variants_audited_ms?: number;
    };
  };
  /**
   * Всё, что нужно для воспроизведения: версии приложения, скиллов, промптов, моделей и рендерера
   */
  versions: {
    app: string;
    skills: VersionRef[];
    prompts?: VersionRef[];
    models: ModelRef[];
    renderer?: VersionRef;
    analyzer?: VersionRef;
    fonts?: {
      family?: string;
      file?: string;
      sha256?: string;
    }[];
    /**
     * версия контрактов, например 1.1
     */
    contracts?: string;
    /**
     * digest образа воркера
     */
    image_digest?: string;
  };
  repairs?: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    repair_job_id: string;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    variant_id: string;
    issue_ids: string[];
    result: "applied" | "partially_applied" | "failed" | "skipped";
    message?: string;
    /**
     * Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
     */
    base_revision?: number;
    /**
     * Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
     */
    new_revision?: number;
    changed_slide_ids?: string[];
  }[];
  warnings?: Warning[];
  error?: Error;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  story_id?: string;
  execution_mode: ExecutionMode;
  /**
   * часть вариантов не готова или завершилась ошибкой; готовые доступны
   */
  partial?: boolean;
  /**
   * правки слайдов по запросу из чата и из визуального редактора: каждая применённая правка создаёт ревизию варианта, как исправление
   */
  edits?: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    edit_job_id: string;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    variant_id: string;
    /**
     * Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
     */
    base_revision: number;
    /**
     * индекс слайда в ревизии, с нуля, как в миниатюрах и отчёте аудита
     */
    slide_index: number;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    slide_id?: string;
    instruction: string;
    result: "applied" | "unchanged" | "failed";
    /**
     * что изменено (applied) или почему слайд оставлен как есть (unchanged)
     */
    change_note?: string;
    /**
     * сообщение об ошибке для failed
     */
    message?: string;
    /**
     * Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
     */
    new_revision?: number;
    changed_slide_ids?: string[];
    /**
     * chat — инструкция, выполненная моделью (по умолчанию); editor — ручные правки объектов без модели
     */
    origin?: "chat" | "editor";
    /**
     * сводка ручных правок для карточки в чате: какие слайды и что изменено
     */
    summary?: string;
  }[];
}
export interface Progress {
  percent?: number;
  message?: string;
}
/**
 * Ошибка задания или операции API
 */
export interface Error {
  code: string;
  message: string;
  stage?:
    | "queued"
    | "analyze"
    | "import"
    | "story"
    | "plan"
    | "compose"
    | "export"
    | "audit"
    | "repair"
    | "finalize"
    | "done";
  retryable?: boolean;
  details?: {
    [k: string]: unknown;
  };
}
export interface StageTiming {
  stage:
    | "queued"
    | "analyze"
    | "import"
    | "story"
    | "plan"
    | "compose"
    | "export"
    | "audit"
    | "repair"
    | "finalize"
    | "done";
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  variant_id?: string;
  status: "pending" | "running" | "done" | "failed" | "skipped";
  started_at?: string;
  duration_ms?: number;
  /**
   * ожидание лимитера провайдера внутри этапа
   */
  quota_wait_ms?: number;
  cache_hit?: boolean;
}
/**
 * Какие слои работали по-настоящему, а какие заглушками. Заглушечный результат не выдаётся за генерацию.
 */
export interface ExecutionMode {
  mode: "real" | "mixed" | "stub";
  /**
   * ключи: parsing.template, parsing.content, brief, generation.story, generation.plan, generation.edit, layout, export, audit.deterministic, audit.contextual
   */
  layers: {
    [k: string]: "real" | "stub" | "replay" | "skipped";
  };
}
/**
 * Общее состояние любого задания: анализ шаблона, импорт содержания, генерация, исправление, правка слайда по запросу, ручные правки из визуального редактора (slide_patch). Отдаётся GET /api/jobs/{id}. Ссылка на результат ведёт на документ своего вида; задания анализа и импорта не заполняют GenerationResult.
 */
export interface JobStatus {
  schema_version: "1.3";
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  job_id: string;
  kind: "template_analysis" | "content_import" | "generation" | "repair" | "slide_edit" | "slide_patch";
  /**
   * succeeded: обязательные проверки завершены и блокирующих проблем нет; needs_review: результат пригоден к просмотру, но есть находки или неполный аудит
   */
  status: "queued" | "running" | "succeeded" | "needs_review" | "failed" | "canceled";
  stage?:
    | "queued"
    | "analyze"
    | "import"
    | "story"
    | "plan"
    | "compose"
    | "export"
    | "audit"
    | "repair"
    | "finalize"
    | "done";
  /**
   * пройденные и текущие этапы с временем; для генерации разбивка по вариантам живёт в GenerationResult
   */
  stages?: StageTiming[];
  progress?: Progress;
  created_at: string;
  started_at?: string;
  finished_at?: string;
  /**
   * ожидание свободного воркера
   */
  queue_wait_ms?: number;
  depends_on?: string[];
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  parent_job_id?: string;
  /**
   * ссылка на результат своего вида; заполняется по мере готовности
   */
  result?: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    template_id?: string;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    package_id?: string;
    generation_result_url?: string;
    template_profile_url?: string;
    content_package_url?: string;
    /**
     * Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
     */
    revision?: number;
    /**
     * для правки слайда: просьба отклонена, ревизия не создана
     */
    unchanged?: boolean;
    /**
     * для правки слайда: что изменено или почему слайд оставлен как есть
     */
    change_note?: string;
    /**
     * для ручных правок (slide_patch): слайды, изменённые в новой ревизии
     */
    changed_slide_ids?: string[];
  };
  error?: Error;
  warnings?: Warning[];
}
/**
 * Проект — одна презентация: выбранный шаблон, файлы, бриф, настройки, задание генерации и лента событий чата. Серверная сущность: интерфейс восстанавливает проект по идентификатору из URL. Карточки ленты ссылаются на шаблоны, пакеты, задания и файлы по идентификаторам и не дублируют данные. Версия 1.3 (этап 6): карточка брифа хранит источник извлечения (модель или правила). Версия 1.2: серверная сущность проекта.
 */
export interface Project {
  schema_version: "1.5";
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  project_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  /**
   * Отсутствие поля равно null.
   */
  template_id?: string | null;
  /**
   * Отсутствие поля равно null.
   */
  package_id?: string | null;
  /**
   * текущее задание генерации Отсутствие поля равно null.
   */
  job_id?: string | null;
  /**
   * Отсутствие поля равно null.
   */
  chosen_variant?: string | null;
  brief: BriefDraft;
  settings: SettingsDraft;
  files: ProjectFile[];
  events: Event[];
}
/**
 * Черновик брифа в интерфейсе: пустая строка означает «не задано»; в ContentPackage уходит с умолчаниями
 */
export interface BriefDraft {
  purpose: "" | "feature" | "product" | "project" | "initiative" | "report" | "other";
  title: string;
  audience: string;
  goal: string;
  language: string;
  tone: string;
  must_include: string[];
  avoid: string[];
}
/**
 * Настройки генерации в интерфейсе; в GenerationRequest переводятся при запуске
 */
export interface SettingsDraft {
  mode: "range" | "exact";
  min: number;
  max: number;
  exact: number;
  variants: ("compact" | "balanced" | "detailed")[];
  contextual: boolean;
  images: boolean;
  /**
   * отсутствие поля равно null
   */
  seed?: number | null;
  force: boolean;
}
/**
 * Файл проекта: шаблон, материал или что-то ещё, что пользователь положил в чат. Байты хранятся на сервере один раз по sha256, запись проекта ссылается на них; вид файла меняется пользователем (PPTX может быть и шаблоном, и материалом). Версия 1.2.
 */
export interface ProjectFile {
  schema_version: "1.2";
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  file_id: string;
  name: string;
  size_bytes: number;
  sha256: string;
  mime: string;
  /**
   * template: шаблон оформления; material: содержание для импорта; other: хранится в проекте без импорта
   */
  kind: "template" | "material" | "other";
  added_at: string;
  /**
   * Результат проверки при загрузке: ZIP и тип содержимого для pptx/docx/xlsx, свой парсер для pdf, текста и изображений; неподдерживаемые типы принимаются без проверки
   */
  check: {
    status: "ok" | "rejected" | "skipped";
    format?: "pptx" | "docx" | "xlsx" | "csv" | "pdf" | "markdown" | "text" | "image" | "other";
    message?: string;
  };
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  template_id?: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  package_id?: string;
}
/**
 * Манифест скилла или агента (skills/<name>/skill.yaml). ТЗ требует версионировать скиллы и агентов и хранить промпты и конфиги отдельными файлами.
 */
export interface SkillManifest {
  name: string;
  version: string;
  stage: "analyze" | "import" | "story" | "plan" | "compose" | "audit" | "repair";
  description: string;
  model_role?: "llm" | "vlm" | "text_to_image" | "none";
  prompts: {
    id: string;
    /**
     * относительный путь к файлу промпта
     */
    path: string;
    version: string;
  }[];
  /**
   * temperature, seed, лимиты, пороги
   */
  params?: {
    [k: string]: unknown;
  };
  /**
   * какой контракт на входе
   */
  input_schema?: string;
  /**
   * какой контракт на выходе
   */
  output_schema?: string;
  changelog?: {
    version: string;
    date?: string;
    note: string;
  }[];
  schema_version: "1.1";
  reasoning?: {
    mode?: "off" | "low" | "medium" | "high" | "provider_default";
    max_output_tokens?: number;
  };
  response_format?: "json_schema" | "json_object" | "text";
}
/**
 * Запрос ручных правок из визуального редактора к ревизии варианта (этап 22). У каждого перечисленного слайда список overrides заменяется целиком: пустой список возвращает слайд к сгенерированному виду; неперечисленные слайды не меняются. order — новый порядок всех слайдов варианта (перестановка slide_id без модели). Применяется детерминированно к SlidePlan базовой ревизии и создаёт новую ревизию тем же конвейером, что правка из чата (compose → export → audit), без вызова модели.
 */
export interface SlidePatch {
  schema_version: "1.0";
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  job_id: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  variant_id: string;
  /**
   * Номер ревизии результата варианта. Растёт после каждого исправления; находки аудита и запросы исправлений привязаны к ревизии.
   */
  base_revision: number;
  slides: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    slide_id: string;
    overrides: Override[];
  }[];
  /**
   * новый порядок слайдов: перестановка всех slide_id плана; отсутствует — порядок не меняется
   */
  order?: string[];
  /**
   * снять или вернуть знак шаблона во всей колоде; отсутствует — не менять
   */
  template_logo?: "keep" | "drop";
}
/**
 * План одного варианта презентации: порядок слайдов, выбранные паттерны и содержание каждого слота. Создаётся слоем генерации, проверяется по схеме и по ёмкости слотов до вёрстки. Версия 1.3 (этап 22): у слайда overrides — ручные правки объектов из визуального редактора, которые композер применяет после заполнения слота; правка из чата заменяет слайд целиком и отбрасывает его overrides с предупреждением overrides_dropped. Версия 1.2 (этап 7): у блоков fit — результат измерения текста по метрикам шрифта после подстановки фактов (выбранный кегль, строки, действие лестницы ёмкости); у таблиц row_offset — часть большого набора данных на этом слайде; у slide_count target — целевое число слайдов варианта внутри диапазона; блок chart допускается в слоте image паттерна с ролью chart (картинка диаграммы в образце заменяется нативной диаграммой). Версия 1.1: ссылка на StoryPlan, покрытие обязательных тезисов, точное число или диапазон слайдов, данные для сопоставления вариантов. Соответствие kind содержимому блока проверяется схемой (allOf/if) и валидаторами.
 */
export interface SlidePlan {
  schema_version: "1.3";
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  plan_id: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  template_id: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  package_id: string;
  language: string;
  /**
   * Три варианта вёрстки одного контента различаются по одной заявленной оси. Ось и обоснование попадают в документацию и в интерфейс сравнения.
   */
  variant: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    variant_id: string;
    axis: "density" | "layout_family" | "visualization" | "narrative_order" | "custom";
    /**
     * например compact | balanced | detailed
     */
    value: string;
    rationale?: string;
  };
  story?: {
    purpose?: string;
    key_takeaway?: string;
    outline?: string[];
  };
  /**
   * @minItems 1
   */
  slides: [Slide1, ...Slide1[]];
  generation_meta: GenerationMeta;
  warnings?: Warning[];
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  story_id: string;
  /**
   * Требование к числу слайдов, унаследованное из запроса: точное число или диапазон; план обязан ему соответствовать. target — целевое число слайдов варианта внутри диапазона (compact ближе к min, detailed к max)
   */
  slide_count: {
    exact?: number;
    min?: number;
    max?: number;
    target?: number;
  };
  /**
   * Покрытие обязательных тезисов StoryPlan: заполняется планировщиком, проверяется валидатором
   */
  coverage: {
    required_thesis_ids: string[];
    covered: {
      /**
       * Стабильный идентификатор. Не содержит пробелов и путей.
       */
      thesis_id: string;
      slide_ids: string[];
      reduction?: string;
    }[];
    missing?: string[];
  };
  /**
   * Данные для сопоставления вариантов: последовательность паттернов и способы визуализации
   */
  comparison?: {
    pattern_sequence?: string[];
    visual_kinds?: string[];
    text_chars_total?: number;
  };
  /**
   * знак шаблона в колоде: keep — как в шаблоне, drop — снять логотипы со всех макетов, мастеров и слайдов (шаблон чужого подразделения без его логотипа)
   */
  template_logo?: "keep" | "drop";
}
export interface Slide1 {
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  slide_id: string;
  order: number;
  /**
   * роль из TemplateProfile.pattern.role
   */
  role?: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  pattern_id: string;
  /**
   * заголовок-вывод, а не название темы
   */
  title: string;
  /**
   * пересказ слайда одним предложением; используется аудитом
   */
  key_message?: string;
  blocks: Block[];
  notes?: string;
  source_refs?: string[];
  fact_refs?: string[];
  thesis_refs?: string[];
  /**
   * что изменено исправлением относительно прошлой ревизии
   */
  revision_note?: string;
  /**
   * ручные правки объектов слайда (этап 22): применяются композером по порядку после заполнения слотов и чистки; адресуют объекты ComposedDeck предыдущей ревизии
   */
  overrides?: Override[];
}
export interface Icon {
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  asset_id?: string;
  /**
   * поисковый запрос к библиотеке иконок, если в шаблоне нет подходящей
   */
  query?: string;
  /**
   * Цвет в формате #RRGGBB
   */
  color?: string;
}
/**
 * Как был создан документ моделью: версии скиллов и промптов, модели, параметры, usage
 */
export interface GenerationMeta {
  skills: VersionRef[];
  prompts?: VersionRef[];
  models: ModelRef[];
  seed?: number;
  temperature?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  cache_hit?: boolean;
  created_at?: string;
}
/**
 * Общий смысловой план презентации, создаётся один раз из ContentPackage и не зависит от шаблона и геометрии. Три SlidePlan ссылаются на него и обязаны покрыть все обязательные тезисы. Версия 1.2 (этап 6): effective_brief — бриф и явные настройки запроса, применённые до построения плана (язык, аудитория, цель, обязательные тезисы, ограничения, число слайдов); coverage — покрытие обязательных фактов и пунктов брифа тезисами; content_hash считается по нормализованному содержанию пакета, effective_brief, модели, промпту и схеме, без идентификаторов заданий и времени.
 */
export interface StoryPlan {
  schema_version: "1.2";
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  story_id: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  package_id: string;
  /**
   * sha256 нормализованного содержания ContentPackage вместе с effective_brief, моделью, версией скилла/промпта и схемой ответа; ключ кэша StoryPlan. Не зависит от package_id, job_id и времени
   */
  content_hash: string;
  language: string;
  purpose: "feature" | "product" | "project" | "initiative" | "report" | "other";
  audience?: string;
  goal?: string;
  /**
   * бриф и явные настройки запроса, применённые до построения плана; смена любого поля меняет content_hash
   */
  effective_brief?: {
    purpose?: "feature" | "product" | "project" | "initiative" | "report" | "other";
    title?: string;
    audience?: string;
    goal?: string;
    language?: string;
    tone?: string;
    must_include?: string[];
    avoid?: string[];
    slide_count?: {
      exact?: number;
      min?: number;
      max?: number;
    };
  };
  /**
   * главный вывод всей презентации одним предложением
   */
  key_takeaway: string;
  /**
   * @minItems 1
   */
  theses: [Thesis, ...Thesis[]];
  /**
   * что допустимо сокращать в компактных вариантах без потери обязательного содержания
   */
  allowed_reductions?: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    thesis_id: string;
    reduction:
      "drop_examples" | "merge_with" | "shorten_wording" | "table_to_chart" | "chart_to_number" | "drop_entirely";
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    target_thesis_id?: string;
    note?: string;
  }[];
  /**
   * допущения режима брифа, отделённые от подтверждённых данных
   */
  assumptions?: string[];
  /**
   * покрытие обязательного содержания тезисами: проверяется кодом до вёрстки
   */
  coverage?: {
    must_keep_facts?: {
      total: number;
      covered: number;
    };
    /**
     * пункты brief.must_include и тезисы, которые их раскрывают; пустой список тезисов — пункт не покрыт
     */
    must_include?: {
      item: string;
      thesis_ids: string[];
    }[];
  };
  generation_meta: GenerationMeta;
  warnings?: Warning[];
}
export interface Thesis {
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  thesis_id: string;
  order: number;
  kind: "section" | "claim" | "evidence" | "conclusion" | "call_to_action" | "context";
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  parent_id?: string;
  /**
   * формулировка-вывод; может содержать {fact:<fact_id>}
   */
  statement: string;
  explanation?: string;
  /**
   * обязателен во всех трёх вариантах
   */
  required: boolean;
  /**
   * block_id из ContentPackage
   */
  source_refs?: string[];
  fact_refs?: string[];
  dataset_refs?: string[];
  asset_refs?: string[];
  suggested_visual?:
    "text" | "bullets" | "number" | "chart" | "table" | "diagram" | "image" | "quote" | "comparison" | "timeline";
}
/**
 * Результат слоя парсинга: дизайн-система, фиксированные элементы, ресурсы и композиционные паттерны шаблона. Полный профиль читают вёрстка и аудит; в модель уходит только llm_digest и выдержки по выбранным паттернам. Версия 1.1: области действия правил, вычисленные стили с источником, геометрия групп и crop, ссылки на объекты слотов, статические и динамические элементы, параметры абзацев. Версия 1.2 (этап 14): у паттерна group_id (группа взаимозаменяемых образцов), tone (светлый или тёмный фон с источником) и style_key (тон | семейство макета | photo или plain) — по ним планировщик выбирает стиль служебных слайдов единым внутри колоды и разным у вариантов.
 */
export interface TemplateProfile {
  schema_version: "1.4";
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  template_id: string;
  /**
   * sha256 исходного файла; ключ кэша профиля вместе с analyzer.version
   */
  template_hash: string;
  source_file: {
    name: string;
    size_bytes: number;
    format: "pptx";
  };
  analyzer: VersionRef;
  created_at?: string;
  slide_size: SlideSize;
  masters?: {
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    master_id: string;
    name?: string;
    theme_name?: string;
  }[];
  layouts: Layout[];
  design_tokens: DesignTokens;
  guides?: Guide[];
  fixed_elements: FixedElement[];
  assets: Asset[];
  patterns: Pattern[];
  guidelines?: Guideline[];
  /**
   * Строки-заглушки, найденные в образцах шаблона: «Заголовок», «Текст», «Lorem ipsum», «ххх%», «Вставить фото». Используются при очистке и в проверке integrity.placeholder_text.
   */
  placeholder_markers?: string[];
  stats: {
    slides: number;
    layouts: number;
    masters: number;
    media?: number;
    native_charts?: number;
    native_tables?: number;
    smartart?: number;
    embedded_fonts?: number;
    notes_with_text?: number;
  };
  /**
   * Компактное текстовое описание профиля для промптов: палитра, шрифты, шкала, список паттернов с ролями и ёмкостью. Ориентир не более 2000 токенов.
   */
  llm_digest?: string;
  warnings?: Warning[];
  /**
   * Классификация каждого слайда шаблона: образец содержания, инструкция по оформлению, каталог ресурсов, пустой. Из паттернов исключаются все, кроме образцов.
   */
  sample_slides?: {
    slide_index: number;
    pptx_slide_part?: string;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    layout_id?: string;
    classification: "content_sample" | "style_guide" | "asset_catalog" | "empty" | "hidden" | "other";
    /**
     * группа похожих образцов, отправленных в VLM вместе
     */
    group_id?: string;
    preview_path?: string;
    confidence?: number;
  }[];
  /**
   * Поля, которые обновляются при смене порядка слайдов: номер слайда, дата, нумерация разделов
   */
  dynamic_fields?: {
    kind: "slide_number" | "date" | "section_index" | "total_slides";
    appears_on: string;
    element_ref?: string;
  }[];
}
export interface Layout {
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  layout_id: string;
  /**
   * имя из cSld@name, например «2 фактоида + текст»
   */
  name: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  master_id?: string;
  placeholders: {
    idx: number;
    type:
      "title" | "ctrTitle" | "subTitle" | "body" | "obj" | "pic" | "chart" | "tbl" | "dt" | "ftr" | "sldNum" | "other";
    bbox?: Bbox1;
    font?: FontSpec;
  }[];
  /**
   * сколько образцовых слайдов шаблона используют этот макет
   */
  sample_slide_count?: number;
}
export interface DesignTokens {
  colors: {
    /**
     * цвета темы мастера: dk1, lt1, dk2, lt2, accent1..accent6, hlink
     */
    theme: {
      /**
       * Цвет в формате #RRGGBB
       */
      [k: string]: string;
    };
    palette: {
      /**
       * Цвет в формате #RRGGBB
       */
      hex: string;
      role: "primary" | "secondary" | "accent" | "neutral" | "background" | "text" | "muted" | "warning";
      usage_count?: number;
      source?: "theme" | "slides" | "layouts" | "master";
      scope?: RuleScope;
      confidence?: number;
      style_source?: StyleSource;
    }[];
  };
  typography: {
    theme_fonts?: {
      major?: string;
      minor?: string;
    };
    fonts: {
      family: string;
      usage_count: number;
      embedded: boolean;
      available_in_renderer?: boolean;
      fallback?: string;
      roles?: ("title" | "body" | "code" | "decorative")[];
      scope?: RuleScope;
      /**
       * фактический файл шрифта в рендерере, по которому измеряется текст
       */
      file?: string;
    }[];
    /**
     * типографическая шкала шаблона; кегли не из шкалы считаются нарушением
     */
    scale: {
      size_pt: number;
      role: "display" | "title" | "subtitle" | "body" | "caption" | "kpi" | "other";
      usage_count?: number;
      scope?: RuleScope;
      confidence?: number;
    }[];
    max_font_families?: number;
  };
  spacing?: {
    /**
     * поля слайда в долях ширины и высоты; выводятся из образцов
     */
    margins?: {
      top: number;
      right: number;
      bottom: number;
      left: number;
    };
    column_grid?: {
      columns?: number;
      gutter?: number;
    };
  };
  /**
   * Пластика шаблона: по ней собственные композиции повторяют вид плашек. Медианы по заметным фигурам образцов.
   */
  shape?: {
    card_geometry?: "rect" | "roundRect";
    /**
     * скругление в долях половины меньшей стороны
     */
    corner_ratio?: number;
    rounded_share?: number;
    stroke_pt?: number;
    shadow_share?: number;
    confidence?: number;
  };
}
/**
 * Область действия правила: где найдено и к чему применимо. Случайный цвет на служебном слайде не становится разрешением использовать его везде.
 */
export interface RuleScope {
  level: "theme" | "master" | "layout" | "pattern" | "text_role" | "slide";
  pattern_ids?: string[];
  text_roles?: ("display" | "title" | "subtitle" | "body" | "caption" | "kpi" | "label" | "code" | "other")[];
  slide_indexes?: number[];
  /**
   * можно ли использовать в новых слайдах
   */
  applies_to_new_content?: boolean;
}
export interface Guide {
  orientation: "horizontal" | "vertical";
  pos: number;
  source: "view_props" | "inferred";
}
/**
 * Элемент, который должен оставаться на месте: логотип, колонтитул, номер страницы, навигационные точки, фон
 */
export interface FixedElement {
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  element_id: string;
  kind: "logo" | "footer" | "page_number" | "background" | "decoration" | "navigation_dots" | "qr_placeholder";
  bbox: Bbox1;
  /**
   * all | layout:<layout_id> | pattern:<pattern_id>
   */
  appears_on: string;
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  asset_id?: string;
  must_not_move?: boolean;
  element_ref?: string;
  /**
   * часть пакета, где живёт элемент: мастер, макет или слайд
   */
  source_part?: string;
}
export interface Asset {
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  asset_id: string;
  kind: "image" | "icon" | "logo" | "photo" | "screenshot" | "mockup" | "chart_image" | "qr" | "background";
  /**
   * путь внутри pptx, например ppt/media/image12.png
   */
  media_path: string;
  sha256: string;
  width_px?: number;
  height_px?: number;
  source_slide_index?: number;
  bbox_on_source?: Bbox1;
  tags?: string[];
  /**
   * можно ли использовать как пиктограмму или иллюстрацию в новых слайдах
   */
  reusable?: boolean;
}
/**
 * Композиционный паттерн. Источник: образцовый слайд шаблона, макет или собственная композиция библиотеки. Паттерны шаблона в отборе идут первыми, собственные подключаются, когда шаблон не покрывает нужную подачу или вместимость.
 */
export interface Pattern {
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  pattern_id: string;
  name?: string;
  role:
    | "title"
    | "agenda"
    | "section_divider"
    | "bullets"
    | "text"
    | "two_column"
    | "cards"
    | "kpi"
    | "numbers"
    | "table"
    | "chart"
    | "timeline"
    | "process"
    | "comparison"
    | "quote"
    | "team"
    | "speaker"
    | "screenshot"
    | "mockup"
    | "pricing"
    | "code"
    | "image_full"
    | "qr"
    | "thanks"
    | "freeform";
  /**
   * Откуда взят паттерн: образцовый слайд шаблона, его макет или собственная композиция библиотеки, построенная из дизайн-кода шаблона на его макете.
   */
  source: {
    kind: "sample_slide" | "layout" | "builtin";
    slide_index?: number;
    /**
     * Стабильный идентификатор. Не содержит пробелов и путей.
     */
    layout_id: string;
    pptx_slide_part?: string;
    /**
     * идентификатор композиции собственной библиотеки (source.kind = builtin)
     */
    composition_id?: string;
  };
  slots: Slot[];
  constraints?: {
    min_items?: number;
    max_items?: number;
    supports?: ("image" | "icon" | "table" | "chart" | "diagram" | "number" | "qr")[];
  };
  tags?: string[];
  /**
   * миниатюра исходного слайда
   */
  preview_path?: string;
  confidence?: number;
  role_source?: "heuristic" | "vlm" | "layout_name" | "manual";
  notes?: string;
  /**
   * объекты образца, которые остаются как есть: декор, фон, логотипы
   */
  static_object_ids?: string[];
  /**
   * объекты образца, удаляемые при незаполненных слотах, например лишние карточки
   */
  removable_object_ids?: string[];
  sequence_hints?: {
    typical_position?: "first" | "early" | "middle" | "late" | "last" | "any";
    max_consecutive?: number;
  };
  /**
   * группа образцов одной сигнатуры (роль, состав слотов, число карточек): члены взаимозаменяемы, планировщик подставляет их в сериях одинаковых композиций
   */
  group_id?: string;
  /**
   * тон фона образца: заливка слайда, закрывающая картинка или фигура, фон макета, мастера или lt1 темы; порог относительной яркости 0,5
   */
  tone?: {
    background: "light" | "dark" | "unknown";
    luminance?: number;
    /**
     * slide_fill, slide_picture, slide_shape, layout_fill, layout_picture, layout_shape, master_fill, master_picture, theme, none
     */
    source: string;
  };
  /**
   * стиль служебного слайда вида «тон|семейство макета|photo или plain»: внутри колоды титул, разделители и финал берутся одного стиля, варианты — разных
   */
  style_key?: string;
  /**
   * объекты нарисованной диаграммы образца (столбики, подписи значений и категорий): при заполнении слота chart вёрстка строит нативную диаграмму и убирает их
   */
  chart_parts?: string[];
}
/**
 * Область паттерна под содержание. Ёмкость считается по метрикам шрифта, а не на глаз.
 */
export interface Slot {
  /**
   * Стабильный идентификатор. Не содержит пробелов и путей.
   */
  slot_id: string;
  kind:
    | "title"
    | "subtitle"
    | "body"
    | "bullets"
    | "number"
    | "label"
    | "caption"
    | "date"
    | "name"
    | "position"
    | "image"
    | "icon"
    | "table"
    | "chart"
    | "diagram"
    | "qr"
    | "code"
    | "footer";
  bbox: Bbox1;
  z_order?: number;
  font?: FontSpec;
  align?: "left" | "center" | "right" | "justify";
  valign?: "top" | "middle" | "bottom";
  capacity?: {
    max_chars?: number;
    max_lines?: number;
    max_items?: number;
    max_words_per_item?: number;
    measured_with?: {
      font_file?: string;
      method?: "font_metrics" | "heuristic";
      margin_ratio?: number;
    };
    confidence?: number;
  };
  required?: boolean;
  /**
   * идентификатор группы повторяющихся карточек; слоты одной группы клонируются вместе
   */
  repeat_group?: string;
  sample_text?: string;
  /**
   * id объекта в исходном образцовом слайде (p:cNvPr@id); вёрстка заменяет содержимое именно этого объекта
   */
  element_ref?: string;
  computed_style?: ComputedTextStyle;
  paragraph_params?: ParagraphParams;
  /**
   * идентификаторы групп от внешней к внутренней, если объект вложен
   */
  group_path?: string[];
  rotation_deg?: number;
  /**
   * crop исходной картинки в долях, для слотов image
   */
  crop?: {
    left?: number;
    top?: number;
    right?: number;
    bottom?: number;
  };
}
/**
 * Параметры абзаца, нужные для измерения вместимости
 */
export interface ParagraphParams {
  line_spacing?: number;
  space_before_pt?: number;
  space_after_pt?: number;
  indent_emu?: number;
  bullet_indent_emu?: number;
  /**
   * внутренние поля рамки в долях ширины и высоты слайда
   */
  insets?: {
    left?: number;
    top?: number;
    right?: number;
    bottom?: number;
  };
  autofit?: "none" | "shrink" | "resize_shape";
  word_wrap?: boolean;
}
/**
 * Правило оформления, найденное текстом в самом шаблоне, например «Перекрытие рядов ±50%, без линий сетки»
 */
export interface Guideline {
  text: string;
  source_slide_index?: number;
  kind: "typography" | "color" | "chart" | "table" | "icons" | "layout" | "general";
}
