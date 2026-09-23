"use client";

import { useEffect, useSyncExternalStore } from "react";

import { api, type EventInput, type ProjectListItem, type ProjectPatch } from "@/lib/api/client";
import type { BriefDraft, Event, Project as ProjectDoc, ProjectFile, SettingsDraft } from "@/lib/api/types";

/**
 * Проекты живут на сервере: интерфейс держит кэш и восстанавливает проект по идентификатору из URL.
 * Изменения применяются к кэшу сразу и уходят на сервер; правки текста собираются в один PATCH.
 */

export type { BriefDraft, ProjectFile, SettingsDraft };

/** Сообщение чата: событие ленты с сужением по виду карточки. Карточки читают состояние по идентификаторам. */
/** Слайд, к которому обращено сообщение: чип в поле ввода, метка в ленте. */
export interface SlideRef {
  job_id: string;
  variant_id: string;
  revision: number;
  slide_index: number;
}

export type ChatMessage =
  | { event_id: string; at: string; role: "user"; kind: "message"; text: string; file_ids: string[]; slide_ref?: SlideRef }
  | { event_id: string; at: string; role: "assistant"; kind: "text"; text: string }
  | { event_id: string; at: string; role: "assistant"; kind: "template_question"; file_id: string; resolved?: PptxAnswer }
  | { event_id: string; at: string; role: "assistant"; kind: "template_card"; template_id: string }
  | { event_id: string; at: string; role: "assistant"; kind: "content_card"; package_id: string; file_ids: string[] }
  | { event_id: string; at: string; role: "assistant"; kind: "brief_card"; understood: string[]; missing_purpose: boolean; brief_source?: "model" | "heuristic" }
  | { event_id: string; at: string; role: "assistant"; kind: "job_card"; job_id: string }
  | { event_id: string; at: string; role: "assistant"; kind: "audit_card"; job_id: string }
  | { event_id: string; at: string; role: "assistant"; kind: "edit_card"; job_id: string; variant_id: string; edit_job_id: string; slide_index: number };

export type Project = Omit<ProjectDoc, "events" | "template_id" | "package_id" | "job_id" | "chosen_variant"> & {
  template_id: string | null;
  package_id: string | null;
  job_id: string | null;
  chosen_variant: string | null;
  events: ChatMessage[];
};

export const DEFAULT_TITLE = "Новая презентация";

export const DEFAULT_BRIEF: BriefDraft = { purpose: "", title: "", audience: "", goal: "", language: "ru", tone: "", must_include: [], avoid: [] };

/** Ответ на вопрос о PPTX: шаблон оформления, готовая презентация как результат или материал. */
export type PptxAnswer = "template" | "deck" | "material";

export const DEFAULT_SETTINGS: SettingsDraft = { mode: "range", min: 10, max: 15, exact: 12, variants: ["compact", "balanced", "detailed"], contextual: true, images: false, seed: null, force: false };

// ---------- кэш и подписки ----------

const projects = new Map<string, Project>();
const missing = new Set<string>();
let list: ProjectListItem[] | null = null;
let listPromise: Promise<void> | null = null;
const loading = new Map<string, Promise<Project | undefined>>();
const listeners = new Set<() => void>();

function notify(): void {
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function normalize(doc: ProjectDoc): Project {
  return {
    ...doc,
    template_id: doc.template_id ?? null,
    package_id: doc.package_id ?? null,
    job_id: doc.job_id ?? null,
    chosen_variant: doc.chosen_variant ?? null,
    brief: { ...DEFAULT_BRIEF, ...doc.brief },
    settings: { ...DEFAULT_SETTINGS, ...doc.settings, seed: doc.settings.seed ?? null },
    files: doc.files ?? [],
    events: (doc.events ?? []) as ChatMessage[],
  };
}

function put(project: Project): Project {
  projects.set(project.project_id, project);
  missing.delete(project.project_id);
  notify();
  return project;
}

// ---------- черновик: экран открыт, проекта на сервере ещё нет ----------

/**
 * Нажатие «Новая презентация» больше не заводит пустой проект: экран открывается на черновике
 * в памяти, а на сервер он попадает при первом действии — сообщении, файле, шаблоне,
 * переименовании или правке брифа. Уйти с пустого экрана можно, не оставив следа.
 *
 * Черновик живёт под временным идентификатором. После первой записи он превращается в
 * настоящий проект, а старый идентификатор продолжает работать: интерфейс успел его
 * запомнить в обработчиках, и переучивать их было бы источником гонок.
 */

const DRAFT_PREFIX = "draft_";
/** Временный идентификатор → настоящий после первой записи. */
const materialized = new Map<string, string>();
/** Создание уже идёт: второй вызов ждёт тот же ответ, а не заводит второй проект. */
const creatingDraft = new Map<string, Promise<string>>();
/** Незаписанный черновик: повторный вход на экран отдаёт его же, а не плодит новые. */
let draftId: string | null = null;

export function isDraft(id: string | null | undefined): boolean {
  return Boolean(id && id.startsWith(DRAFT_PREFIX));
}

/** Идентификатор в кэше: после первой записи черновик живёт под настоящим. */
function canonical(id: string): string {
  return materialized.get(id) ?? id;
}

/** Черновик для экрана новой презентации: тот же, пока его не записали на сервер. */
export function startDraft(): Project {
  const existing = draftId ? projects.get(draftId) : undefined;
  if (draftId && existing && !materialized.has(draftId)) return existing;
  const id = `${DRAFT_PREFIX}${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const now = new Date().toISOString();
  const project: Project = {
    schema_version: "1.5",
    project_id: id,
    title: DEFAULT_TITLE,
    created_at: now,
    updated_at: now,
    template_id: null,
    package_id: null,
    job_id: null,
    chosen_variant: null,
    brief: { ...DEFAULT_BRIEF },
    settings: { ...DEFAULT_SETTINGS },
    files: [],
    events: [],
  };
  draftId = id;
  projects.set(id, project);
  notify();
  return project;
}

/** Идентификатор для запроса на сервер: черновик по дороге становится настоящим проектом. */
async function serverId(id: string): Promise<string> {
  const known = materialized.get(id);
  if (known) return known;
  if (!isDraft(id)) return id;
  const pending = creatingDraft.get(id);
  if (pending) return pending;
  const promise = (async () => {
    const draft = projects.get(id);
    const doc = await api.projects.create({ title: draft?.title ?? DEFAULT_TITLE });
    // Всё, что успели набрать в черновике, остаётся на экране: лента, файлы, бриф и настройки
    // уедут на сервер тем же патчем, что и вызвал запись.
    const created = normalize(doc);
    const merged: Project = draft
      ? { ...created, title: draft.title, brief: draft.brief, settings: draft.settings, files: draft.files, events: draft.events }
      : created;
    materialized.set(id, merged.project_id);
    projects.delete(id);
    if (draftId === id) draftId = null;
    put(merged);
    list = [listItem(merged), ...(list ?? [])];
    notify();
    return merged.project_id;
  })();
  creatingDraft.set(id, promise);
  try {
    return await promise;
  } finally {
    creatingDraft.delete(id);
  }
}

function listItem(project: Project): ProjectListItem {
  return {
    project_id: project.project_id,
    title: project.title,
    created_at: project.created_at,
    updated_at: project.updated_at,
    template_id: project.template_id,
    package_id: project.package_id,
    job_id: project.job_id,
    chosen_variant: project.chosen_variant,
    brief: project.brief,
    settings: project.settings,
    files_count: project.files.length,
    template_name: null,
    job_status: null,
    thumbnail_url: null,
    slide_count: null,
  } as ProjectListItem;
}

// ---------- чтение ----------

const EMPTY_LIST: ProjectListItem[] = [];

export async function refreshProjects(): Promise<ProjectListItem[]> {
  if (!listPromise) {
    listPromise = api.projects
      .list()
      .then((items) => {
        list = items;
      })
      .catch(() => {
        list = list ?? [];
      })
      .finally(() => {
        listPromise = null;
        notify();
      });
  }
  await listPromise;
  return list ?? EMPTY_LIST;
}

/** Список проектов с сервера, новые сверху; до загрузки пустой. Пока есть незавершённые задания, обновляется раз в несколько секунд. */
export function useProjects(): { items: ProjectListItem[]; loaded: boolean } {
  const items = useSyncExternalStore(subscribe, () => list ?? EMPTY_LIST, () => EMPTY_LIST);
  const loaded = useSyncExternalStore(subscribe, () => list !== null, () => false);
  useEffect(() => {
    void refreshProjects();
  }, []);
  const active = items.some((i) => i.job_status === "queued" || i.job_status === "running");
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void refreshProjects(), 4000);
    return () => clearInterval(timer);
  }, [active]);
  return { items, loaded };
}

export async function loadProject(id: string): Promise<Project | undefined> {
  // Черновика на сервере нет: он живёт только в кэше вкладки.
  if (isDraft(id) && !materialized.has(id)) return projects.get(id);
  id = canonical(id);
  const pending = loading.get(id);
  if (pending) return pending;
  const promise = api.projects
    .get(id)
    .then((doc) => put(normalize(doc)))
    .catch(() => {
      missing.add(id);
      notify();
      return undefined;
    })
    .finally(() => loading.delete(id));
  loading.set(id, promise);
  return promise;
}

/** Проект из кэша с загрузкой с сервера; status: loading → ready | missing. */
export function useProject(id: string | null): { project: Project | undefined; status: "loading" | "ready" | "missing" } {
  const project = useSyncExternalStore(subscribe, () => (id ? projects.get(canonical(id)) : undefined), () => undefined);
  const isMissing = useSyncExternalStore(subscribe, () => (id ? missing.has(canonical(id)) : false), () => false);
  useEffect(() => {
    if (id && !projects.has(canonical(id))) void loadProject(id);
  }, [id]);
  if (!id) return { project: undefined, status: "missing" };
  if (project) return { project, status: "ready" };
  return { project: undefined, status: isMissing ? "missing" : "loading" };
}

/** Актуальное состояние проекта прямо из кэша: оркестратору чата нужно свежее состояние сразу после записи. */
export function getProject(id: string): Project | undefined {
  return projects.get(canonical(id));
}

export async function findProjectByJob(jobId: string): Promise<ProjectListItem | undefined> {
  const items = await refreshProjects();
  return items.find((p) => p.job_id === jobId);
}

// ---------- запись ----------

export async function createProject(patch: { title?: string; job_id?: string } = {}): Promise<Project> {
  const doc = await api.projects.create(patch);
  const project = put(normalize(doc));
  list = [{ ...doc, files_count: 0, template_name: null, job_status: null, thumbnail_url: null, slide_count: null, template_id: project.template_id, package_id: project.package_id, job_id: project.job_id, chosen_variant: project.chosen_variant }, ...(list ?? [])];
  notify();
  return project;
}

const pendingPatches = new Map<string, { patch: ProjectPatch; timer: ReturnType<typeof setTimeout> }>();
/** Связи проекта уходят на сервер сразу; текст брифа и настройки собираются в один запрос. */
const IMMEDIATE_KEYS = new Set<keyof ProjectPatch>(["template_id", "package_id", "job_id", "chosen_variant"]);

function flushPatch(id: string, keepalive = false): void {
  const entry = pendingPatches.get(id);
  if (!entry) return;
  pendingPatches.delete(id);
  clearTimeout(entry.timer);
  const init = keepalive ? { keepalive: true } : undefined;
  // Первая правка черновика и заводит проект на сервере: до неё записывать нечего.
  void serverId(id)
    .then((real) => api.projects.patch(real, entry.patch, init))
    .catch(() => {
      /* сервер не ответил: кэш уже обновлён, повтор при следующем изменении */
    });
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", () => {
    for (const id of [...pendingPatches.keys()]) flushPatch(id, true);
  });
}

/** Применяет изменение к кэшу сразу и отправляет на сервер; частые правки текста объединяются. */
export function updateProject(id: string, patch: Partial<Project> | ((p: Project) => Partial<Project>)): void {
  id = canonical(id);
  const current = projects.get(id);
  if (!current) return;
  const delta = typeof patch === "function" ? patch(current) : patch;
  const next: Project = { ...current, ...delta, updated_at: new Date().toISOString() };
  projects.set(id, next);
  if (list) list = list.map((item) => (item.project_id === id ? { ...item, title: next.title, template_id: next.template_id, package_id: next.package_id, job_id: next.job_id, chosen_variant: next.chosen_variant, updated_at: next.updated_at } : item));
  notify();
  const serverPatch: ProjectPatch = {};
  for (const key of ["title", "template_id", "package_id", "job_id", "chosen_variant", "brief", "settings"] as const) {
    if (key in delta) Object.assign(serverPatch, { [key]: next[key] });
  }
  if (Object.keys(serverPatch).length === 0) return;
  const existing = pendingPatches.get(id);
  if (existing) clearTimeout(existing.timer);
  const merged = { ...(existing?.patch ?? {}), ...serverPatch };
  const immediate = Object.keys(serverPatch).some((k) => IMMEDIATE_KEYS.has(k as keyof ProjectPatch));
  pendingPatches.set(id, { patch: merged, timer: setTimeout(() => flushPatch(id), immediate ? 0 : 400) });
  if (immediate) flushPatch(id);
}

export async function deleteProject(id: string): Promise<void> {
  // Незаписанный черновик удаляется из памяти: на сервере его нет.
  const real = isDraft(id) && !materialized.has(id) ? null : canonical(id);
  projects.delete(canonical(id));
  if (draftId === id) draftId = null;
  if (list && real) list = list.filter((p) => p.project_id !== real);
  notify();
  if (real) await api.projects.delete(real).catch(() => undefined);
}

// ---------- лента событий ----------

const tempIds = new Map<string, Promise<string>>();
// Callers may still hold the optimistic ID after an unrelated LLM request finishes.
const savedEventIds = new Map<string, string>();

/** Событие появляется в кэше сразу с временным идентификатором и заменяется серверным после ответа. */
export function appendMessage(projectId: string, message: EventInput): ChatMessage {
  const tempId = `tmp_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const optimistic = { event_id: tempId, at: new Date().toISOString(), ...message } as ChatMessage;
  updateProject(projectId, (p) => ({ events: [...p.events, optimistic] }));
  const promise = serverId(projectId)
    .then((real) => api.projects.appendEvent(real, message))
    .then((saved) => {
      savedEventIds.set(tempId, saved.event_id);
      if (savedEventIds.size > 500) savedEventIds.delete(savedEventIds.keys().next().value!);
      updateProject(projectId, (p) => ({ events: p.events.map((e) => (e.event_id === tempId ? ({ ...saved } as ChatMessage) : e)) }));
      return saved.event_id;
    })
    .catch(() => tempId)
    .finally(() => tempIds.delete(tempId));
  tempIds.set(tempId, promise);
  return optimistic;
}

export async function patchMessage(projectId: string, messageId: string, patch: Partial<Event>): Promise<void> {
  const resolved = (await tempIds.get(messageId)) ?? savedEventIds.get(messageId) ?? messageId;
  updateProject(projectId, (p) => ({ events: p.events.map((m) => (m.event_id === resolved || m.event_id === messageId ? ({ ...m, ...patch } as ChatMessage) : m)) }));
  if (!resolved.startsWith("tmp_")) {
    await serverId(projectId)
      .then((real) => api.projects.patchEvent(real, resolved, patch))
      .catch(() => undefined);
  }
}

/** Ask about a saved message; only the backend persists the answer. */
export async function askAssistant(projectId: string, messageId: string) {
  const eventId = (await tempIds.get(messageId)) ?? savedEventIds.get(messageId) ?? messageId;
  if (eventId.startsWith("tmp_")) throw new Error("Сообщение не сохранилось. Повторите отправку.");
  const real = await serverId(projectId);
  const project = getProject(real);
  if (!project) throw new Error("Проект не загружен.");
  // Brief extraction may have just updated a debounced draft. Send it before reading context.
  // Drain the queued patch so its stale settings cannot overwrite a mode selected by chat.
  const pending = pendingPatches.get(real);
  if (pending) {
    clearTimeout(pending.timer);
    pendingPatches.delete(real);
  }
  await api.projects.patch(real, { ...pending?.patch, brief: project.brief, settings: project.settings });
  const result = await api.chat(real, eventId);
  updateProject(real, (p) => ({ events: p.events.some((e) => e.event_id === result.event.event_id)
    ? p.events : [...p.events, result.event as ChatMessage] }));
  return result;
}

// ---------- файлы проекта ----------

/** Загружает файлы на сервер в момент добавления в чат; байты дальше передаются по идентификаторам. */
export async function addProjectFiles(projectId: string, files: File[]): Promise<ProjectFile[]> {
  if (files.length === 0) return [];
  const rows = await api.projects.uploadFiles(await serverId(projectId), files);
  updateProject(projectId, (p) => ({ files: [...p.files.filter((f) => !rows.some((r) => r.file_id === f.file_id)), ...rows] }));
  return rows;
}

export function patchProjectFile(projectId: string, fileId: string, patch: Partial<ProjectFile>): void {
  updateProject(projectId, (p) => ({ files: p.files.map((f) => (f.file_id === fileId ? { ...f, ...patch } : f)) }));
  const serverPatch: Partial<Pick<ProjectFile, "kind" | "template_id" | "package_id">> = {};
  if (patch.kind) serverPatch.kind = patch.kind;
  if (patch.template_id) serverPatch.template_id = patch.template_id;
  if (patch.package_id) serverPatch.package_id = patch.package_id;
  if (Object.keys(serverPatch).length) {
    void serverId(projectId)
      .then((real) => api.projects.patchFile(real, fileId, serverPatch))
      .catch(() => undefined);
  }
}

export async function removeProjectFile(projectId: string, fileId: string): Promise<void> {
  updateProject(projectId, (p) => ({ files: p.files.filter((f) => f.file_id !== fileId) }));
  await serverId(projectId)
    .then((real) => api.projects.deleteFile(real, fileId))
    .catch(() => undefined);
}

/** Перечитывает проект с сервера: после операций, которые сервер отмечает сам (шаблон, пакет). */
export async function refreshProject(id: string): Promise<void> {
  // Черновик перечитывать неоткуда: на сервере его ещё нет.
  if (isDraft(id) && !materialized.has(id)) return;
  try {
    put(normalize(await api.projects.get(canonical(id))));
  } catch {
    /* сеть недоступна: остаёмся на кэше */
  }
}
