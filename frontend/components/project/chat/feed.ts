"use client";

import { TERMINAL_STATES } from "@/lib/api/client";
import type { GenerationResult } from "@/lib/api/types";
import type { ChatMessage, Project } from "@/lib/state/projects";

/**
 * Откуда пришла правка, видно по идентификатору её задания: `rep_` ставит аудит, `patch_` —
 * визуальный редактор, `edit_` — просьба словами. Отдельного поля у события нет намеренно:
 * схема проекта (`project.schema.json`) не принимает лишних полей, а идентификатор приходит с
 * сервера и переживает перезагрузку.
 */
export function editOrigin(editJobId: string): "audit" | "editor" | "chat" {
  if (editJobId.startsWith("rep_")) return "audit";
  if (editJobId.startsWith("patch_")) return "editor";
  return "chat";
}

/**
 * Шаги заменённого задания в ленте не показываются. Сообщения читают живое состояние, а у
 * прошлой сборки его уже нет: после третьей генерации лента превращалась в три строки «это
 * задание заменено новым». Разговор и файлы остаются целиком, артефакты прошлых сборок — на сервере.
 */
export function isStaleStep(message: ChatMessage, jobId: string | null): boolean {
  if (message.role !== "assistant" || !jobId) return false;
  if (message.kind === "job_card" || message.kind === "audit_card" || message.kind === "edit_card") {
    return message.job_id !== jobId;
  }
  return false;
}

/** Отчёты аудита остаются в данных проекта, но не в ленте. Исправление находок теперь
 * запускает чат («исправь замечания»), поэтому его карточка видна, как правка. */
function isAuditStep(message: ChatMessage): boolean {
  return message.role === "assistant" && message.kind === "audit_card";
}

/** Сообщение без текста — бывшая запись о загрузке файла: файлы видны во вкладке «Файлы». */
function isUploadNotice(message: ChatMessage): boolean {
  return message.role === "user" && !message.text?.trim() && !message.slide_ref;
}

export function isVisibleProjectMessage(message: ChatMessage, jobId: string | null): boolean {
  return !isStaleStep(message, jobId) && !isAuditStep(message) && !isUploadNotice(message);
}

/**
 * «Открыть как презентацию» — вариант original: один и тот же PPTX стал и шаблоном, и
 * содержанием. Пока результата нет, это видно по файлу проекта.
 */
export function isDeckJob(project: Project, result: GenerationResult | null): boolean {
  if (result) return Boolean(result.request?.settings?.variants?.includes("original"));
  return project.files.some((f) => f.kind === "template" && f.template_id === project.template_id && f.package_id === project.package_id);
}

/** Предупреждения задания, которые лента говорит словами: пропущенные слайды готовой
 * презентации, диаграммы-картинки, ставшие редактируемыми, и нехватка содержания на
 * число слайдов, о котором просили. */
export const JOB_WARNINGS = new Set(["original_slides_skipped", "chart_images", "slide_count_short"]);

/**
 * Готовая презентация открывается строкой под «Открываю…», там же тихо идёт разбор в фоне и
 * появляется фраза о диаграммах. Ход сборки в ленте это не дублирует: сообщение появляется,
 * только если разбор не удался, в слайдах что-то изменилось или закончилась проверка моделью.
 */
export function deckJobHasNews(result: GenerationResult | null): boolean {
  if (!result || !TERMINAL_STATES.has(result.status) || result.status === "canceled") return false;
  if (result.status === "failed" || result.request?.settings?.run_contextual_audit === true) return true;
  return Boolean(result.warnings?.some((w) => JOB_WARNINGS.has(w.code) && w.code !== "chart_images"));
}
