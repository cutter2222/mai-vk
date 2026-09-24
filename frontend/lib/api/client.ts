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
  SlidePatch,
  StoryPlan,
  TemplateProfile,
} from "./types";

/** Ответы операций, не описанных отдельной схемой (см. contracts/README.md, раздел HTTP API). */
export interface OfficeDocument {
  id: string;
  source: string;
  title: string;
  revision: number;
  active_key: string | null;
  error: string | null;
  revisions: { revision: number; sha256: string; saved_at: number }[];
  normalizations?: { revision: number; raw_sha256: string; parts: string[] }[];
}

export interface OfficePreview {
  revision: number;
  slides: string[];
  ratio: number;
}

export interface OfficeObjectTarget { slide: number; shape_id: string }
/** Знак шаблона на всех слайдах: заменить картинкой из файлов проекта или убрать. */
export interface OfficeLogoAction {
  action: "replace" | "remove";
  file_id?: string;
}
/** Картинка для текущего слайда редактора: файл проекта или ресурс шаблона. */
export type OfficeImageSource =
  | { project_id: string; file_id: string }
  | { template_id: string; asset_id: string };
/** Подписанная сервером команда `docEditor.insertImage`. */
export interface OfficeImageCommand {
  c: "add";
  images: { fileType: string; url: string }[];
  token: string;
}
export interface OfficeObject extends OfficeObjectTarget {
  label: string;
  /** Имя фигуры в PPTX — по нему живой редактор сообщает, что выделено. */
  name?: string;
  kind: string;
  bbox: { x: number; y: number; width: number; height: number };
  z: number;
  hollow: boolean;
  /** Группы над объектом, от внешней к внутренней; пусто — объект верхнего уровня. */
  group_path?: string[];
  rotation?: number;
  /** Тип плейсхолдера (title, body…), если объект — плейсхолдер. */
  placeholder?: string | null;
}
/** Объект, выделенный в живом редакторе: слайд, имя фигуры и её рамка в миллиметрах. У фигуры
 * внутри группы (`in_group`) рамку редактор отдаёт от левого верхнего угла группы. */
export interface OfficeLiveTarget {
  slide: number;
  name: string;
  box?: { x: number; y: number; width: number; height: number };
  in_group?: boolean;
}
/** Открытая в редакторе офисная копия: ассистент отвечает о её содержимом. */
export interface OpenOfficeDocument { document_id: string; revision: number }
/** Слайд новой ревизии варианта — на место слайда офисной копии (номера с единицы). */
export interface OfficeApplySlide {
  job_id: string;
  variant_id: string;
  artifact_revision: number;
  slide: number;
  source_slide: number;
}
export interface OfficeSelection extends OfficeObjectTarget {
  documentId: string;
  revision: number;
  label: string;
  objects: (OfficeObjectTarget & { label: string })[];
}

export interface HealthResponse {
  status: "ok" | "degraded" | "down";
  workers: { analysis: number; generation: number };
  valkey_ok: boolean;
  renderer_ok: boolean;
  version: string;
  /** Какие слои работают по-настоящему, а какие пока заглушки. */
  execution_mode?: { mode: "real" | "mixed" | "stub"; layers: Record<string, string> };
  /** Провайдер модели и роли: что именно отвечает за текст и за картинки. */
  provider?: {
    name: string;
    host?: string;
    configured: boolean;
    probed?: boolean;
    roles?: Record<string, { model?: string; reasoning?: string; verified?: boolean }>;
  };
}

export interface CapabilitiesResponse {
  contracts_version: string;
  execution_mode: { mode: "real" | "mixed" | "stub"; layers: Record<string, string> };
  /** speech — голосовой ввод в чате: сервис распознавания отвечает и модель на месте. */
  features: { generate_images: boolean; contextual_audit: boolean; html_export: boolean; speech?: boolean };
  speech?: { language: string; max_seconds: number };
  limits: { max_upload_mb: number; max_content_files: number; slide_count_max: number };
}

/** Распознанная фраза голосового ввода. */
export interface SpeechTranscript {
  text: string;
  duration_ms: number;
  infer_ms: number;
  model: string;
}

export interface TemplateListItem {
  template_id: string;
  name: string;
  status: "queued" | "running" | "succeeded" | "failed";
  slide_count?: number;
  pattern_count?: number;
  /** Имя миниатюры первого образца для карточки библиотеки (см. templates.assetUrl). */
  preview?: string;
  /** Первые цвета палитры шаблона (до пяти, hex): стиль виден прямо в списке. */
  colors?: string[];
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
  office: {
    capabilities: () => request<{ enabled: boolean; script_url?: string }>("/office/capabilities"),
    templateConfig: (id: string) => request<{ script_url: string; config: Record<string, unknown> }>(`/office/templates/${encodeURIComponent(id)}/config`, json({})),
    create: (jobId: string, artifact: string) => request<OfficeDocument>("/office/documents", json({ job_id: jobId, artifact })),
    templateCopy: (projectId: string, templateId: string) => request<OfficeDocument>(`/office/projects/${encodeURIComponent(projectId)}/template`, json({ template_id: templateId })),
    get: (id: string) => request<OfficeDocument>(`/office/documents/${encodeURIComponent(id)}`),
    preview: (id: string, revision: number) => request<OfficePreview>(`/office/documents/${encodeURIComponent(id)}/preview/${revision}`),
    objects: (id: string, revision: number) => request<{ revision: number; objects: OfficeObject[] }>(`/office/documents/${encodeURIComponent(id)}/objects/${revision}`),
    previewUrl: (id: string, revision: number, name: string) => `${API_BASE}/office/documents/${encodeURIComponent(id)}/preview/${revision}/${encodeURIComponent(name)}`,
    edit: (id: string, revision: number, instruction: string, target?: OfficeObjectTarget | OfficeObjectTarget[] | OfficeLiveTarget, logo?: OfficeLogoAction) => request<{ document: OfficeDocument; changed: boolean; message: string }>(`/office/documents/${encodeURIComponent(id)}/edit`, json({ revision, instruction, ...(logo ? { logo } : Array.isArray(target) ? { targets: target } : target && "name" in target ? { live_target: target } : target ? { target } : {}) })),
    /** Слайд, пересобранный правкой из чата, встаёт в офисную копию; остальные слайды не меняются. */
    applySlide: (id: string, revision: number, body: OfficeApplySlide) => request<{ document: OfficeDocument; changed: boolean; message: string }>(`/office/documents/${encodeURIComponent(id)}/apply-slide`, json({ revision, ...body })),
    config: (id: string) => request<{ script_url: string; config: Record<string, unknown> }>(`/office/documents/${encodeURIComponent(id)}/config`, json({})),
    /** Команда вставки картинки на текущий слайд: сервер проверяет источник и подписывает ссылку. */
    imageCommand: (id: string, source: OfficeImageSource) => request<OfficeImageCommand>(`/office/documents/${encodeURIComponent(id)}/images`, json(
      "file_id" in source ? { project_id: source.project_id, file_id: source.file_id } : { template_id: source.template_id, asset_id: source.asset_id },
    )),
    downloadUrl: (id: string, revision: number, format: "pptx" | "pdf" | "html" = "pptx") => `${API_BASE}/office/documents/${encodeURIComponent(id)}/download/${revision}${format === "pptx" ? "" : `?format=${format}`}`,
  },
  health: () => request<HealthResponse>("/health"),
  capabilities: () => request<CapabilitiesResponse>("/capabilities"),

  /**
   * Голосовой ввод: фраза (WAV PCM16 моно 16 кГц, до 25 с) → текст; аудио сервер не хранит.
   * `partial` — черновик недоговорённой фразы: тот же ответ, в журнале сервера отмечен отдельно.
   */
  speech: {
    transcribe: (wav: Blob, signal?: AbortSignal, options: { partial?: boolean } = {}) => {
      const form = new FormData();
      form.append("audio", wav, "phrase.wav");
      return request<SpeechTranscript>(`/speech/transcribe${options.partial ? "?partial=1" : ""}`, { method: "POST", body: form, signal });
    },
  },

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
    /** Сообщение с содержанием — материалом «Текст из чата.md»: сервер восстанавливает строки и разделы «Слайд N». */
    addText: (id: string, text: string) => request<ProjectFile>(`/projects/${encodeURIComponent(id)}/files/text`, json({ text })),
    patchFile: (id: string, fileId: string, patch: Partial<Pick<ProjectFile, "kind" | "template_id" | "package_id">>) =>
      request<ProjectFile>(`/projects/${encodeURIComponent(id)}/files/${encodeURIComponent(fileId)}`, json(patch, "PATCH")),
    deleteFile: (id: string, fileId: string) => request<void>(`/projects/${encodeURIComponent(id)}/files/${encodeURIComponent(fileId)}`, { method: "DELETE" }),
    /** Байты файла проекта: картинки и PDF открываются во вкладке, остальное скачивается. */
    fileUrl: (id: string, fileId: string) => `${API_BASE}/projects/${encodeURIComponent(id)}/files/${encodeURIComponent(fileId)}/content`,
    /** Миниатюра для сетки «Файлы» (картинка, первая страница PDF, обложка PPTX); 404 — показать значок типа. */
    thumbnailUrl: (id: string, fileId: string) => `${API_BASE}/projects/${encodeURIComponent(id)}/files/${encodeURIComponent(fileId)}/thumbnail`,
  },

  templates: {
    list: () => request<TemplateListItem[]>("/templates"),
    get: (id: string) => request<TemplateDetail>(`/templates/${encodeURIComponent(id)}`),
    /** Шаблон из уже загруженного файла проекта: байты второй раз не пересылаются. */
    upload: (fileId: string) => request<{ template_id: string; job_id: string; cached: boolean }>("/templates", json({ file_id: fileId })),
    /** Шаблон прямо из файла: библиотека пополняется без захода в проект. */
    uploadFile: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return request<{ template_id: string; job_id: string; cached: boolean }>("/templates", {
        method: "POST",
        body: form,
        headers: mockFilesHeader([file]),
      });
    },
    /** Убирает шаблон из библиотеки; проекты, которые им пользовались, остаются без шаблона. */
    delete: (id: string) => request<void>(`/templates/${encodeURIComponent(id)}`, { method: "DELETE" }),
    assetUrl: (id: string, name: string) => `${API_BASE}/templates/${encodeURIComponent(id)}/assets/${name}`,
    /** Байты ресурса профиля шаблона (иконка, логотип, картинка) для холста и панели редактора. */
    mediaUrl: (id: string, assetId: string) => `${API_BASE}/templates/${encodeURIComponent(id)}/media/${encodeURIComponent(assetId)}`,
    /** Миниатюра ресурса (WebP до 480 px) для сетки «Из шаблона»: оригиналы бывают по 2000 px. */
    mediaThumbnailUrl: (id: string, assetId: string) => `${API_BASE}/templates/${encodeURIComponent(id)}/media/${encodeURIComponent(assetId)}/thumbnail`,
    /** Адрес профиля целиком: открыть JSON в новой вкладке. */
    detailUrl: (id: string) => `${API_BASE}/templates/${encodeURIComponent(id)}`,
    sourceUrl: (id: string) => `${API_BASE}/templates/${encodeURIComponent(id)}/source`,
  },

  /** Бриф из свободного сообщения чата. Поля, которых нет в тексте, сервер не заполняет. */
  brief: {
    extract: (text: string, brief?: Record<string, unknown>) => request<BriefExtractResponse>("/brief", json({ text, brief })),
  },

  chat: (projectId: string, eventId: string, office?: OpenOfficeDocument | null) => request<{ reply: string; options: string[]; source: "model" | "rules"; event: Event }>("/chat", json({ project_id: projectId, event_id: eventId, ...(office ? { office } : {}) })),

  content: {
    /** Контент-пакет из файлов проекта по идентификаторам и брифа. */
    create: (fileIds: string[], brief?: Record<string, unknown>) =>
      request<{ package_id: string; job_id: string; cached: boolean }>("/content", json({ file_ids: fileIds, brief })),
    get: (id: string) => request<ContentDetail>(`/content/${encodeURIComponent(id)}`),
    /** Картинка контент-пакета по пути из package.assets[].path. */
    assetUrl: (id: string, name: string) => `${API_BASE}/content/${encodeURIComponent(id)}/assets/${name}`,
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
    /** Ручные правки из визуального редактора: новая ревизия варианта без модели (документ slide_patch). */
    patch: (jobId: string, variantId: string, baseRevision: number, slides: SlidePatch["slides"], order?: string[], templateLogo?: "keep" | "drop") =>
      request<{ patch_job_id: string }>(
        `/generations/${encodeURIComponent(jobId)}/variants/${encodeURIComponent(variantId)}/patches`,
        json({ base_revision: baseRevision, slides, ...(order ? { order } : {}), ...(templateLogo ? { template_logo: templateLogo } : {}) }),
      ),
    artifactUrl: (jobId: string, name: string) => `${API_BASE}/generations/${encodeURIComponent(jobId)}/artifacts/${name}`,
    /** JSON-артефакт ревизии (план, описание собранной колоды) по имени из манифеста. */
    artifactJson: <T,>(jobId: string, name: string) => request<T>(`/generations/${encodeURIComponent(jobId)}/artifacts/${name}`),
  },

  jobs: {
    get: (id: string) => request<JobStatus>(`/jobs/${encodeURIComponent(id)}`),
    cancel: (id: string) => request<void>(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
    retry: (id: string) => request<{ job_id: string }>(`/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  },
};

export const TERMINAL_STATES = new Set(["succeeded", "needs_review", "failed", "canceled"]);
