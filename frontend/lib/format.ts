export function formatMs(ms?: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} мс`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} с`;
  const m = Math.floor(s / 60);
  return `${m} мин ${Math.round(s - m * 60)} с`;
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
};

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

export function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(d);
}
