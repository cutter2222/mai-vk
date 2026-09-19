/**
 * Заглушка серверных проектов: список, проект, лента событий и файлы.
 * Хранится в sessionStorage вкладки, как и остальное состояние заглушки.
 */

import type { ProjectListItem } from "@/lib/api/client";
import type { Event, Project, ProjectFile } from "@/lib/api/types";

import { nextId } from "./state";

const KEY = "pd:mock-projects";

interface Store {
  projects: Project[];
}

function load(): Store {
  if (typeof window === "undefined") return { projects: [] };
  try {
    const raw = window.sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Store) : { projects: [] };
  } catch {
    return { projects: [] };
  }
}

function save(): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(projectStore));
  } catch {
    /* переполнение хранилища не критично для заглушки */
  }
}

export const projectStore: Store = load();

const iso = () => new Date().toISOString();

export const DEFAULT_BRIEF: Project["brief"] = { purpose: "", title: "", audience: "", goal: "", language: "ru", tone: "", must_include: [], avoid: [] };
export const DEFAULT_SETTINGS: Project["settings"] = { mode: "range", min: 10, max: 15, exact: 12, variants: ["compact", "balanced", "detailed"], contextual: true, images: false, seed: null, force: false };

export function createProject(body: { title?: string; job_id?: string } = {}): Project {
  const now = iso();
  const project: Project = {
    schema_version: "1.5",
    project_id: nextId("prj"),
    title: body.title?.trim() || "Новая презентация",
    created_at: now,
    updated_at: now,
    template_id: null,
    package_id: null,
    job_id: body.job_id ?? null,
    chosen_variant: null,
    brief: { ...DEFAULT_BRIEF },
    settings: { ...DEFAULT_SETTINGS },
    files: [],
    events: [],
  };
  projectStore.projects.unshift(project);
  save();
  return project;
}

export function getProject(id: string): Project | undefined {
  return projectStore.projects.find((p) => p.project_id === id);
}

export function patchProject(id: string, patch: Partial<Project>): Project | undefined {
  const p = getProject(id);
  if (!p) return undefined;
  Object.assign(p, patch, { updated_at: iso() });
  save();
  return p;
}

export function deleteProject(id: string): boolean {
  const before = projectStore.projects.length;
  projectStore.projects = projectStore.projects.filter((p) => p.project_id !== id);
  save();
  return projectStore.projects.length < before;
}

/** Шаблон удалён из библиотеки: проекты и их файлы теряют ссылку, как на сервере. */
export function detachTemplate(templateId: string): void {
  for (const p of projectStore.projects) {
    if (p.template_id === templateId) Object.assign(p, { template_id: null, updated_at: iso() });
    p.files.forEach((f) => {
      if (f.template_id === templateId) delete f.template_id;
    });
  }
  save();
}

export function appendEvent(id: string, payload: Omit<Event, "event_id" | "at">): Event | undefined {
  const p = getProject(id);
  if (!p) return undefined;
  const event = { event_id: nextId("evt"), at: iso(), ...payload } as Event;
  p.events.push(event);
  p.updated_at = event.at;
  save();
  return event;
}

export function patchEvent(id: string, eventId: string, patch: Partial<Event>): Event | undefined {
  const p = getProject(id);
  const event = p?.events.find((e) => e.event_id === eventId);
  if (!event) return undefined;
  Object.assign(event, patch);
  save();
  return event;
}

const FORMAT_BY_EXT: Record<string, NonNullable<ProjectFile["check"]["format"]>> = { pptx: "pptx", docx: "docx", xlsx: "xlsx", csv: "csv", pdf: "pdf", md: "markdown", txt: "text", png: "image", jpg: "image", jpeg: "image" };
const MATERIAL = new Set(["docx", "xlsx", "csv", "pdf", "markdown", "text", "image"]);

export function addFile(id: string, name: string, size: number): ProjectFile | undefined {
  const p = getProject(id);
  if (!p) return undefined;
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  const format = FORMAT_BY_EXT[ext] ?? "other";
  const sha = Array.from(`${name}:${size}`).reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7).toString(16).padStart(8, "0").repeat(8);
  const existing = p.files.find((f) => f.name === name && f.size_bytes === size);
  if (existing) return existing;
  const file: ProjectFile = {
    schema_version: "1.2",
    file_id: nextId("file"),
    name,
    size_bytes: size,
    sha256: sha,
    mime: "application/octet-stream",
    kind: MATERIAL.has(format) ? "material" : "other",
    added_at: iso(),
    check: format === "other" ? { status: "skipped", format: "other" } : { status: "ok", format },
  };
  p.files.push(file);
  p.updated_at = file.added_at;
  save();
  return file;
}

export function patchFile(id: string, fileId: string, patch: Partial<ProjectFile>): ProjectFile | undefined {
  const p = getProject(id);
  const file = p?.files.find((f) => f.file_id === fileId);
  if (!file) return undefined;
  Object.assign(file, patch);
  save();
  return file;
}

export function findFile(fileId: string): { project: Project; file: ProjectFile } | undefined {
  for (const project of projectStore.projects) {
    const file = project.files.find((f) => f.file_id === fileId);
    if (file) return { project, file };
  }
  return undefined;
}

export function deleteFile(id: string, fileId: string): boolean {
  const p = getProject(id);
  if (!p) return false;
  const before = p.files.length;
  p.files = p.files.filter((f) => f.file_id !== fileId);
  save();
  return p.files.length < before;
}

export function listItem(p: Project, extra: Pick<ProjectListItem, "job_status" | "thumbnail_url" | "slide_count" | "template_name">): ProjectListItem {
  return {
    project_id: p.project_id,
    title: p.title,
    created_at: p.created_at,
    updated_at: p.updated_at,
    template_id: p.template_id ?? null,
    package_id: p.package_id ?? null,
    job_id: p.job_id ?? null,
    chosen_variant: p.chosen_variant ?? null,
    files_count: p.files.length,
    ...extra,
  };
}
