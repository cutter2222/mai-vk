import { API_BASE, API_MODE } from "./config";
import type {
  AuditReport,
  ContentPackage,
  GenerationRequest,
  GenerationResult,
  JobStatus,
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
  created_at: string;
}

export interface TemplateDetail {
  status: TemplateListItem["status"];
  job_id: string;
  name: string;
  profile?: TemplateProfile;
  previews: string[];
}

export interface ContentDetail {
  status: TemplateListItem["status"];
  job_id: string;
  package?: ContentPackage;
}

export interface BriefExtractResponse {
  brief: Partial<{ purpose: string; title: string; audience: string; goal: string; language: string; tone: string; must_include: string[]; avoid: string[] }>;
  slide_count?: { exact?: number; min?: number; max?: number };
  variants?: string[];
  /** Какие поля действительно найдены в тексте. */
  understood: string[];
}

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

  templates: {
    list: () => request<TemplateListItem[]>("/templates"),
    get: (id: string) => request<TemplateDetail>(`/templates/${encodeURIComponent(id)}`),
    upload: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<{ template_id: string; job_id: string; cached: boolean }>("/templates", { method: "POST", body: form, headers: mockFilesHeader([file]) });
    },
    assetUrl: (id: string, name: string) => `${API_BASE}/templates/${encodeURIComponent(id)}/assets/${name}`,
  },

  /** Бриф из свободного сообщения чата. Поля, которых нет в тексте, сервер не заполняет. */
  brief: {
    extract: (text: string, brief?: Record<string, unknown>) => request<BriefExtractResponse>("/brief", json({ text, brief })),
  },

  content: {
    create: (files: File[], brief?: Record<string, unknown>) => {
      const form = new FormData();
      files.forEach((f) => form.append("files", f));
      if (brief) form.append("brief", JSON.stringify(brief));
      return request<{ package_id: string; job_id: string }>("/content", { method: "POST", body: form, headers: mockFilesHeader(files) });
    },
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
    artifactUrl: (jobId: string, name: string) => `${API_BASE}/generations/${encodeURIComponent(jobId)}/artifacts/${name}`,
  },

  jobs: {
    get: (id: string) => request<JobStatus>(`/jobs/${encodeURIComponent(id)}`),
    cancel: (id: string) => request<void>(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
    retry: (id: string) => request<{ job_id: string }>(`/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  },
};

export const TERMINAL_STATES = new Set(["succeeded", "needs_review", "failed", "canceled"]);
