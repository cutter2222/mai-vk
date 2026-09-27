export function formatMs(ms?: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} мс`;
  // Округляем до секунд сразу: иначе 119,6 с превращались в «1 мин 60 с».
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} с`;
  return `${Math.floor(s / 60)} мин ${s % 60} с`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

export function formatNumber(n?: number | null): string {
  if (n == null) return "—";
  return new Intl.NumberFormat("ru-RU").format(n);
}

export const STAGE_LABELS: Record<string, string> = {
  queued: "В очереди",
  analyze: "Анализ шаблона",
  import: "Импорт содержания",
  story: "Смысловой план",
  plan: "Планы вариантов",
  compose: "Сборка PPTX",
  export: "Экспорт",
  audit: "Аудит",
  repair: "Исправления",
  finalize: "Завершение",
  done: "Готово",
};

export const STATUS_LABELS: Record<string, string> = {
  queued: "В очереди",
  running: "Выполняется",
  succeeded: "Готово",
  needs_review: "Требует проверки",
  failed: "Ошибка",
  canceled: "Отменено",
  pending: "Ожидает",
  ready: "Готов",
  complete: "Завершён",
  partial: "Частично",
  skipped: "Пропущен",
};

export const VARIANT_LABELS: Record<string, string> = {
  compact: "Компактный",
  balanced: "Сбалансированный",
  detailed: "Подробный",
  original: "Исходная презентация",
};

/** Вариант, который сервер собирает первым (`jobs.PRIMARY_VARIANT`). */
export const PRIMARY_VARIANT = "balanced";

/** Порядок сборки, как `jobs.ordered_variants`: основной вариант первым, остальные в порядке запроса. */
export function buildOrder<T>(items: T[], id: (item: T) => string = String): T[] {
  return [...items.filter((item) => id(item) === PRIMARY_VARIANT), ...items.filter((item) => id(item) !== PRIMARY_VARIANT)];
}

export const SEVERITY_LABELS: Record<string, string> = {
  blocking: "Блокирует",
  error: "Ошибка",
  warning: "Предупреждение",
  info: "Заметка",
};

export const CATEGORY_LABELS: Record<string, string> = {
  layout: "Вёрстка",
  template: "Шаблон",
  density: "Плотность",
  integrity: "Целостность",
  content: "Содержание",
};

export const PATTERN_ROLE_LABELS: Record<string, string> = {
  title: "Титульный",
  agenda: "Оглавление",
  section_divider: "Разделитель",
  bullets: "Список",
  text: "Текст",
  two_column: "Две колонки",
  cards: "Карточки",
  kpi: "Показатели",
  numbers: "Числа",
  table: "Таблица",
  chart: "График",
  timeline: "Таймлайн",
  process: "Процесс",
  comparison: "Сравнение",
  quote: "Цитата",
  team: "Команда",
  speaker: "Спикер",
  screenshot: "Скриншот",
  mockup: "Мокап",
  pricing: "Цены",
  code: "Код",
  image_full: "Изображение",
  qr: "QR-код",
  thanks: "Финальный",
  freeform: "Свободный",
};

/** Классификация слайдов файла шаблона (TemplateProfile.sample_slides). */
export const SLIDE_CLASS_LABELS: Record<string, string> = {
  content_sample: "Образец содержания",
  style_guide: "Инструкция по оформлению",
  asset_catalog: "Каталог ресурсов",
  empty: "Пустой",
  hidden: "Скрытый",
  other: "Другое",
};

export const SLOT_KIND_LABELS: Record<string, string> = {
  title: "Заголовок",
  subtitle: "Подзаголовок",
  body: "Текст",
  bullets: "Список",
  number: "Число",
  label: "Подпись",
  caption: "Подпись к объекту",
  date: "Дата",
  name: "Имя",
  position: "Должность",
  image: "Изображение",
  icon: "Иконка",
  table: "Таблица",
  chart: "График",
  diagram: "Схема",
  qr: "QR-код",
  code: "Код",
  footer: "Колонтитул",
};

export const FIXED_KIND_LABELS: Record<string, string> = {
  logo: "Логотип",
  footer: "Колонтитул",
  page_number: "Номер слайда",
  background: "Фон",
  decoration: "Декор",
  navigation_dots: "Навигация",
  qr_placeholder: "Место под QR",
};

export const ASSET_KIND_LABELS: Record<string, string> = {
  image: "Изображение",
  icon: "Иконка",
  logo: "Логотип",
  photo: "Фото",
  screenshot: "Скриншот",
  mockup: "Мокап",
  chart_image: "Картинка графика",
  qr: "QR-код",
  background: "Фон",
};

export const COLOR_ROLE_LABELS: Record<string, string> = {
  primary: "основной",
  secondary: "дополнительный",
  accent: "акцент",
  neutral: "нейтральный",
  background: "фон",
  text: "текст",
  muted: "приглушённый",
  warning: "предупреждение",
};

export const GUIDELINE_KIND_LABELS: Record<string, string> = {
  typography: "Типографика",
  color: "Цвет",
  chart: "Графики",
  table: "Таблицы",
  icons: "Иконки",
  layout: "Вёрстка",
  general: "Общее",
};

export const ROLE_SOURCE_LABELS: Record<string, string> = {
  heuristic: "эвристика",
  vlm: "модель",
  layout_name: "имя макета",
  manual: "вручную",
};

export const DYNAMIC_FIELD_LABELS: Record<string, string> = {
  slide_number: "Номер слайда",
  date: "Дата",
  section_index: "Номер раздела",
  total_slides: "Всего слайдов",
};

/** Доля слайда в проценты: 0.054 → «5,4 %». */
export function formatRatio(v: number): string {
  return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(v * 100)} %`;
}

export function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(d);
}

/** Склонение при числе: «1 находка», «2 находки», «5 находок». */
export function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}
