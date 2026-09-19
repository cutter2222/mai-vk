import { API_BASE, API_MODE } from "./config";

export { API_BASE };
import type {
  AuditReport,
  BriefExtract,
  ContentPackage,
  Event,
  GenerationRequest,
  GenerationResult,
  JobStatus,
  Project,
  ProjectFile,
  StoryPlan,
  TemplateProfile,
} from "./types";

/** Ответы операций, не описанных отдельной схемой (см. contracts/README.md, раздел HTTP API). */
export interface HealthResponse {
  status: "ok" | "degraded" | "down";
  workers: { analysis: number; generation: number };
  valkey_ok: boolean;
  renderer_ok: boolean;
  version: string;
}

export interface CapabilitiesResponse {
  contracts_version: string;
  execution_mode: { mode: "real" | "mixed" | "stub"; layers: Record<string, string> };
  features: { generate_images: boolean; contextual_audit: boolean; html_export: boolean };
  limits: { max_upload_mb: number; max_content_files: number; slide_count_max: number };
}

export interface TemplateListItem {
  template_id: string;
  name: string;
  status: "queued" | "running" | "succeeded" | "failed";
  slide_count?: number;
  pattern_count?: number;
  /** Имя миниатюры первого образца для карточки библиотеки (см. templates.assetUrl). */
  preview?: string;
  created_at: string;
}

export interface TemplateDetail {
  status: TemplateListItem["status"];
  job_id: string;
  name: string;
  profile?: TemplateProfile;
  previews: string[];
  error?: ApiErrorBody["error"];
  /** Время задания анализа: начало, конец и длительность чтения образцов. */
  timing?: { created_at?: string; started_at?: string; finished_at?: string; duration_ms?: number };
}

export interface ContentDetail {
  status: TemplateListItem["status"];
  job_id: string;
  package?: ContentPackage;
  error?: ApiErrorBody["error"];
}

export type BriefExtractResponse = BriefExtract;

/** Элемент списка проектов: проект без файлов и ленты плюс состояние для карточки. */
export interface ProjectListItem {
  project_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  template_id: string | null;
  package_id: string | null;
  job_id: string | null;
  chosen_variant: string | null;
  files_count: number;
  template_name: string | null;
  job_status: "queued" | "running" | "succeeded" | "needs_review" | "failed" | "canceled" | null;
  thumbnail_url: string | null;
  slide_count: number | null;
}

export type ProjectPatch = Partial<Pick<Project, "title" | "template_id" | "package_id" | "job_id" | "chosen_variant" | "brief" | "settings">>;
export type EventInput = Omit<Event, "event_id" | "at">;

export interface ApiErrorBody {
  error: { code: string; message: string; stage?: string; retryable?: boolean; details?: Record<string, unknown> };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody | undefined, fallback: string) {
    super(body?.error.message ?? fallback);
    this.name = "ApiError";
    this.status = status;
    this.code = body?.error.code ?? `http_${status}`;
    this.details = body?.error.details;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let body: ApiErrorBody | undefined;
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      body = undefined;
    }
    throw new ApiError(response.status, body, `Ошибка запроса ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * В режиме заглушек Safari передаёт multipart в service worker без бинарных частей,
 * поэтому имена и размеры файлов дублируются заголовком. В рабочем режиме заголовок не добавляется.
 */
function mockFilesHeader(files: File[]): HeadersInit | undefined {
  if (API_MODE !== "mock") return undefined;
  return { "X-Mock-Files": encodeURIComponent(JSON.stringify(files.map((f) => ({ name: f.name, size: f.size })))) };
}

function json(body: unknown, method = "POST"): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const api = {
  health: () => request<HealthResponse>("/health"),
  capabilities: () => request<CapabilitiesResponse>("/capabilities"),

  /** Проекты живут на сервере: список, проект по идентификатору, лента событий и файлы. */
  projects: {
    list: () => request<ProjectListItem[]>("/projects"),
    create: (body: { title?: string; job_id?: string } = {}) => request<Project>("/projects", json(body)),
    get: (id: string) => request<Project>(`/projects/${encodeURIComponent(id)}`),
    patch: (id: string, patch: ProjectPatch, init?: RequestInit) => request<Project>(`/projects/${encodeURIComponent(id)}`, { ...json(patch, "PATCH"), ...init }),
    delete: (id: string) => request<void>(`/projects/${encodeURIComponent(id)}`, { method: "DELETE" }),
    appendEvent: (id: string, event: EventInput) => request<Event>(`/projects/${encodeURIComponent(id)}/events`, json(event)),
    patchEvent: (id: string, eventId: string, patch: Partial<Event>) =>
      request<Event>(`/projects/${encodeURIComponent(id)}/events/${encodeURIComponent(eventId)}`, json(patch, "PATCH")),
    uploadFiles: (id: string, files: File[]) => {
      const form = new FormData();
      files.forEach((f) => form.append("files", f));
      return request<ProjectFile[]>(`/projects/${encodeURIComponent(id)}/files`, { method: "POST", body: form, headers: mockFilesHeader(files) });
    },
    patchFile: (id: string, fileId: string, patch: Partial<Pick<ProjectFile, "kind" | "template_id" | "package_id">>) =>
      request<ProjectFile>(`/projects/${encodeURIComponent(id)}/files/${encodeURIComponent(fileId)}`, json(patch, "PATCH")),
    deleteFile: (id: string, fileId: string) => request<void>(`/projects/${encodeURIComponent(id)}/files/${encodeURIComponent(fileId)}`, { method: "DELETE" }),
  },

  templates: {
    list: () => request<TemplateListItem[]>("/templates"),
    get: (id: string) => request<TemplateDetail>(`/templates/${encodeURIComponent(id)}`),
    /** Шаблон из уже загруженного файла проекта: байты второй раз не пересылаются. */
    upload: (fileId: string) => request<{ template_id: string; job_id: string; cached: boolean }>("/templates", json({ file_id: fileId })),
    /** Убирает шаблон из библиотеки; проекты, которые им пользовались, остаются без шаблона. */
    delete: (id: string) => request<void>(`/templates/${encodeURIComponent(id)}`, { method: "DELETE" }),
    assetUrl: (id: string, name: string) => `${API_BASE}/templates/${encodeURIComponent(id)}/assets/${name}`,
    /** Адрес профиля целиком: открыть JSON в новой вкладке. */
    detailUrl: (id: string) => `${API_BASE}/templates/${encodeURIComponent(id)}`,
  },

  /** Бриф из свободного сообщения чата. Поля, которых нет в тексте, сервер не заполняет. */
  brief: {
    extract: (text: string, brief?: Record<string, unknown>) => request<BriefExtractResponse>("/brief", json({ text, brief })),
  },

  content: {
    /** Контент-пакет из файлов проекта по идентификаторам и брифа. */
    create: (fileIds: string[], brief?: Record<string, unknown>) =>
      request<{ package_id: string; job_id: string; cached: boolean }>("/content", json({ file_ids: fileIds, brief })),
    get: (id: string) => request<ContentDetail>(`/content/${encodeURIComponent(id)}`),
  },

  generations: {
    create: (body: GenerationRequest) => request<{ job_id: string }>("/generations", json(body)),
    get: (jobId: string) => request<GenerationResult>(`/generations/${encodeURIComponent(jobId)}`),
    story: (jobId: string) => request<StoryPlan>(`/generations/${encodeURIComponent(jobId)}/story`),
    audit: (jobId: string, variantId: string, revision?: number) =>
      request<AuditReport>(
        `/generations/${encodeURIComponent(jobId)}/variants/${encodeURIComponent(variantId)}/audit${
          revision ? `?revision=${revision}` : ""
        }`,
      ),
    repair: (jobId: string, variantId: string, baseRevision: number, issueIds: string[]) =>
      request<{ repair_job_id: string }>(
        `/generations/${encodeURIComponent(jobId)}/variants/${encodeURIComponent(variantId)}/repairs`,
        json({ base_revision: baseRevision, issue_ids: issueIds }),
      ),
    /** Правка одного слайда по инструкции из чата: новая ревизия варианта, как у исправления. */
    edit: (jobId: string, variantId: string, baseRevision: number, slideIndex: number, instruction: string) =>
      request<{ edit_job_id: string }>(
        `/generations/${encodeURIComponent(jobId)}/variants/${encodeURIComponent(variantId)}/edits`,
        json({ base_revision: baseRevision, slide_index: slideIndex, instruction }),
      ),
    artifactUrl: (jobId: string, name: string) => `${API_BASE}/generations/${encodeURIComponent(jobId)}/artifacts/${name}`,
  },

  jobs: {
    get: (id: string) => request<JobStatus>(`/jobs/${encodeURIComponent(id)}`),
    cancel: (id: string) => request<void>(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
    retry: (id: string) => request<{ job_id: string }>(`/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  },
};

export const TERMINAL_STATES = new Set(["succeeded", "needs_review", "failed", "canceled"]);
