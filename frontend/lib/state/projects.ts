"use client";

import { useSyncExternalStore } from "react";

/**
 * Реестр проектов. Проект — одна презентация: выбранный шаблон, содержание, настройки и задание генерации.
 * Живёт в localStorage браузера: у API пока нет списка заданий, а задания и файлы хранятся на сервере.
 */

export interface BriefDraft {
  purpose: string;
  title: string;
  audience: string;
  goal: string;
  language: string;
  tone: string;
  must_include: string[];
  avoid: string[];
}

export interface SettingsDraft {
  mode: "range" | "exact";
  min: number;
  max: number;
  exact: number;
  variants: string[];
  contextual: boolean;
  images: boolean;
  seed: number | null;
  force: boolean;
}

/** Файл проекта: шаблон, материал или что-то ещё, что пользователь положил в чат. Байты живут в fileStore до перезагрузки. */
export interface ProjectFile {
  id: string;
  name: string;
  size: number;
  mime: string;
  kind: "template" | "material" | "other";
  added_at: string;
  /** Для шаблона — идентификатор на сервере после загрузки. */
  template_id?: string;
  /** Для материала — пакет, в который он импортирован. */
  package_id?: string;
}

/** Сообщение чата. Карточки не хранят данные, а ссылаются на состояние проекта и сервера по идентификаторам. */
export type ChatMessage =
  | { id: string; at: string; role: "user"; text: string; file_ids: string[] }
  | { id: string; at: string; role: "assistant"; kind: "text"; text: string }
  | { id: string; at: string; role: "assistant"; kind: "template_question"; file_id: string; resolved?: "template" | "material" }
  | { id: string; at: string; role: "assistant"; kind: "template_card"; template_id: string }
  | { id: string; at: string; role: "assistant"; kind: "content_card"; package_id: string; file_ids: string[] }
  | { id: string; at: string; role: "assistant"; kind: "brief_card"; understood: string[]; missing_purpose: boolean }
  | { id: string; at: string; role: "assistant"; kind: "job_card"; job_id: string }
  | { id: string; at: string; role: "assistant"; kind: "audit_card"; job_id: string };

export interface Project {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  template_id: string | null;
  package_id: string | null;
  job_id: string | null;
  chosen_variant: string | null;
  brief: BriefDraft;
  settings: SettingsDraft;
  files: ProjectFile[];
  messages: ChatMessage[];
}

export const DEFAULT_TITLE = "Новая презентация";

export const DEFAULT_BRIEF: BriefDraft = { purpose: "", title: "", audience: "", goal: "", language: "ru", tone: "", must_include: [], avoid: [] };

export const DEFAULT_SETTINGS: SettingsDraft = { mode: "range", min: 10, max: 15, exact: 12, variants: ["compact", "balanced", "detailed"], contextual: true, images: false, seed: null, force: false };

const KEY = "pd:projects";
const EMPTY: Project[] = [];

let cache: Project[] | null = null;
const listeners = new Set<() => void>();

function read(): Project[] {
  if (cache) return cache;
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed = raw ? (JSON.parse(raw) as Project[]) : [];
    cache = parsed.map((p) => ({ ...p, brief: { ...DEFAULT_BRIEF, ...p.brief }, settings: { ...DEFAULT_SETTINGS, ...p.settings }, files: p.files ?? [], messages: p.messages ?? [] }));
  } catch {
    cache = [];
  }
  return cache;
}

function write(list: Project[]): void {
  cache = list;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    /* хранилище недоступно: реестр живёт до перезагрузки */
  }
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY) {
      cache = null;
      listener();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/** Список проектов, новые сверху. На сервере и до гидратации пустой. */
export function useProjects(): Project[] {
  return useSyncExternalStore(subscribe, read, () => EMPTY);
}

export function useProject(id: string | null): Project | undefined {
  const list = useProjects();
  return id ? list.find((p) => p.id === id) : undefined;
}

const newId = () => `prj_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

export function createProject(patch: Partial<Project> = {}): Project {
  const nowIso = new Date().toISOString();
  const project: Project = {
    id: newId(),
    title: DEFAULT_TITLE,
    created_at: nowIso,
    updated_at: nowIso,
    template_id: null,
    package_id: null,
    job_id: null,
    chosen_variant: null,
    brief: { ...DEFAULT_BRIEF },
    settings: { ...DEFAULT_SETTINGS },
    files: [],
    messages: [],
    ...patch,
  };
  write([project, ...read()]);
  return project;
}

export function updateProject(id: string, patch: Partial<Project> | ((p: Project) => Partial<Project>)): void {
  const list = read();
  const idx = list.findIndex((p) => p.id === id);
  if (idx < 0) return;
  const current = list[idx];
  const next = { ...current, ...(typeof patch === "function" ? patch(current) : patch), updated_at: new Date().toISOString() };
  const copy = [...list];
  copy[idx] = next;
  write(copy);
}

export function deleteProject(id: string): void {
  write(read().filter((p) => p.id !== id));
}

/** Актуальное состояние проекта прямо из реестра: оркестратору чата нужно свежее состояние сразу после записи, не дожидаясь рендера. */
export function getProject(id: string): Project | undefined {
  return read().find((p) => p.id === id);
}

export function findProjectByJob(jobId: string): Project | undefined {
  return read().find((p) => p.job_id === jobId);
}

export const newMessageId = () => `msg_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
export const newFileId = () => `file_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

/** Omit по объединению типов схлопывает варианты, поэтому распределяем вручную. */
type MessageInput = ChatMessage extends infer M ? (M extends ChatMessage ? Omit<M, "id" | "at"> : never) : never;

export function appendMessage(projectId: string, message: MessageInput): ChatMessage {
  const full = { id: newMessageId(), at: new Date().toISOString(), ...message } as ChatMessage;
  updateProject(projectId, (p) => ({ messages: [...p.messages, full] }));
  return full;
}

export function patchMessage(projectId: string, messageId: string, patch: Partial<ChatMessage>): void {
  updateProject(projectId, (p) => ({ messages: p.messages.map((m) => (m.id === messageId ? ({ ...m, ...patch } as ChatMessage) : m)) }));
}

export function addProjectFiles(projectId: string, files: ProjectFile[]): void {
  updateProject(projectId, (p) => ({ files: [...p.files, ...files] }));
}

export function patchProjectFile(projectId: string, fileId: string, patch: Partial<ProjectFile>): void {
  updateProject(projectId, (p) => ({ files: p.files.map((f) => (f.id === fileId ? { ...f, ...patch } : f)) }));
}

export function removeProjectFile(projectId: string, fileId: string): void {
  updateProject(projectId, (p) => ({ files: p.files.filter((f) => f.id !== fileId) }));
}
