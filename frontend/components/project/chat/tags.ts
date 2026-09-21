"use client";

import type { ChatMessage } from "@/lib/state/projects";

/**
 * Метки ленты проекта. Чат — единственное место, где видно всё, что произошло: файлы, разбор
 * шаблона, материалы, задача, генерация, аудит и правки. Когда шагов становится много, лента
 * перестаёт читаться, поэтому наверху стоит ряд меток: он же оглавление, он же фильтр.
 */
export type ChatTag = "message" | "template" | "materials" | "brief" | "job" | "audit" | "edit";

/** Метка на самом сообщении: «#аудит» под текстом — видно, к какому шагу оно относится. */
export const TAG_HASH: Record<ChatTag, string> = {
  message: "",
  template: "#шаблон",
  materials: "#материалы",
  brief: "#задача",
  job: "#генерация",
  audit: "#аудит",
  edit: "#правки",
};

export const TAG_LABELS: Record<ChatTag, string> = {
  message: "Сообщения",
  template: "Шаблон",
  materials: "Материалы",
  brief: "Задача",
  job: "Генерация",
  audit: "Аудит",
  edit: "Правки",
};

/** Порядок меток — порядок шагов работы, а не частота: так ряд читается как оглавление. */
export const TAG_ORDER: ChatTag[] = ["template", "materials", "brief", "job", "audit", "edit", "message"];

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

export function tagOf(message: ChatMessage): ChatTag {
  if (message.role === "user") return "message";
  switch (message.kind) {
    case "template_question":
    case "template_card":
      return "template";
    case "content_card":
      return "materials";
    case "brief_card":
      return "brief";
    case "job_card":
      return "job";
    case "audit_card":
      return "audit";
    case "edit_card":
      // Исправление находок — продолжение разговора об аудите: под меткой «правки» оно
      // терялось ровно тогда, когда человек смотрит на аудит и нажимает «Исправить».
      return editOrigin(message.edit_job_id) === "audit" ? "audit" : "edit";
    default:
      return "message";
  }
}

/**
 * Шаги заменённого задания в ленте не показываются. Сообщения читают живое состояние, а у
 * прошлой сборки его уже нет: после третьей генерации лента превращалась в три строки «это
 * задание заменено новым» и пустые строки с метками. Разговор и файлы остаются целиком,
 * артефакты прошлых сборок — на сервере.
 */
export function isStaleStep(message: ChatMessage, jobId: string | null): boolean {
  if (message.role !== "assistant" || !jobId) return false;
  if (message.kind === "job_card" || message.kind === "audit_card" || message.kind === "edit_card") {
    return message.job_id !== jobId;
  }
  return false;
}

/** Исторические отчёты остаются в данных проекта, но не в интерфейсе редактора. */
export function isVisibleProjectMessage(message: ChatMessage, jobId: string | null): boolean {
  return !isStaleStep(message, jobId) && tagOf(message) !== "audit";
}

/** Сколько сообщений под каждой меткой: пустые метки в ряд не попадают. */
export function countTags(messages: ChatMessage[]): Record<ChatTag, number> {
  const counts = { message: 0, template: 0, materials: 0, brief: 0, job: 0, audit: 0, edit: 0 };
  for (const message of messages) counts[tagOf(message)] += 1;
  return counts;
}
