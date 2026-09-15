/**
 * Состояние заглушки backend. Живёт в памяти вкладки и имитирует ход заданий по времени:
 * анализ шаблона, импорт содержания, генерацию с тремя вариантами, аудит и исправления.
 * Сценарии для тестов: шаблон с именем, содержащим «fail», роняет вариант detailed;
 * job «job_unknown» не существует; исправление по устаревшей ревизии даёт 409.
 */

import auditExample from "./data/audit_report.json";
import packageExample from "./data/content_package.json";
import resultExample from "./data/generation_result.json";
import planExample from "./data/slide_plan.json";
import storyExample from "./data/story_plan.json";
import profileExample from "./data/template_profile.json";
import type { AuditReport, ContentPackage, GenerationRequest, GenerationResult, JobStatus, SlidePlan, StoryPlan, TemplateProfile } from "@/lib/api/types";

type Status = "queued" | "running" | "succeeded" | "needs_review" | "failed" | "canceled";

export interface MockTemplate {
  template_id: string;
  name: string;
  job_id: string;
  created_at: string;
  startedAt: number;
  durationMs: number;
  profile: TemplateProfile;
}

export interface MockPackage {
  package_id: string;
  job_id: string;
  startedAt: number;
  durationMs: number;
  pkg: ContentPackage;
  fileNames: string[];
  mode: "package" | "brief" | "mixed";
}

interface RevisionRecord {
  revision: number;
  created_at: string;
  changed_slide_ids: string[];
  audit: AuditReport;
  fixedIssueIds: string[];
}

export interface MockVariant {
  variant_id: "compact" | "balanced" | "detailed";
  startedAt: number;
  planMs: number;
  composeMs: number;
  exportMs: number;
  auditMs: number;
  fail: boolean;
  revisions: RevisionRecord[];
  currentRevision: number;
  slideCount: number;
}

export interface MockGeneration {
  job_id: string;
  request: GenerationRequest;
  template: MockTemplate;
  pkg: MockPackage;
  createdAt: number;
  canceled: boolean;
  variants: MockVariant[];
  storyMs: number;
  story: StoryPlan;
  plan: SlidePlan;
}

export interface MockRepair {
  repair_job_id: string;
  job_id: string;
  variant_id: string;
  base_revision: number;
  issue_ids: string[];
  startedAt: number;
  durationMs: number;
  applied: boolean;
}

const SPEED = Number(typeof window !== "undefined" ? window.localStorage.getItem("mock_speed") ?? "1" : "1") || 1;
const ms = (n: number) => Math.round(n / SPEED);

interface Store {
  templates: Map<string, MockTemplate>;
  packages: Map<string, MockPackage>;
  generations: Map<string, MockGeneration>;
  repairs: Map<string, MockRepair>;
  seq: number;
}

const STORAGE_KEY = "pd:mock-state";

/** Состояние переживает перезагрузку вкладки: хранится в sessionStorage, ссылки восстанавливаются по идентификаторам. */
function loadStore(): Store {
  const empty: Store = { templates: new Map(), packages: new Map(), generations: new Map(), repairs: new Map(), seq: 1 };
  if (typeof window === "undefined") return empty;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return empty;
    const data = JSON.parse(raw) as { templates: MockTemplate[]; packages: MockPackage[]; generations: Array<Omit<MockGeneration, "template" | "pkg"> & { template_id: string; package_id: string }>; repairs: MockRepair[]; seq: number };
    const s: Store = { ...empty, seq: data.seq };
    data.templates.forEach((t) => s.templates.set(t.template_id, t));
    data.packages.forEach((p) => s.packages.set(p.package_id, p));
    data.generations.forEach((g) => {
      const template = s.templates.get(g.template_id);
      const pkg = s.packages.get(g.package_id);
      if (template && pkg) s.generations.set(g.job_id, { ...g, template, pkg });
    });
    data.repairs.forEach((r) => s.repairs.set(r.repair_job_id, r));
    return s;
  } catch {
    return empty;
  }
}

export function persistStore(): void {
  if (typeof window === "undefined") return;
  try {
    const data = {
      templates: [...store.templates.values()],
      packages: [...store.packages.values()],
      generations: [...store.generations.values()].map(({ template, pkg, ...g }) => ({ ...g, template_id: template.template_id, package_id: pkg.package_id })),
      repairs: [...store.repairs.values()],
      seq: store.seq,
    };
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    /* переполнение хранилища не критично для заглушки */
  }
}

export const store: Store = loadStore();

export const nextId = (prefix: string) => `${prefix}_${(store.seq++).toString(36)}${Date.now().toString(36).slice(-3)}`;
const iso = (t: number) => new Date(t).toISOString();
const now = () => Date.now();

// ---------- шаблоны ----------

export function createTemplate(name: string, sizeBytes: number): { template: MockTemplate; cached: boolean } {
  const existing = [...store.templates.values()].find((t) => t.name === name);
  if (existing) return { template: existing, cached: true };
  const template_id = nextId("tpl");
  const profile = structuredClone(profileExample) as unknown as TemplateProfile;
  profile.template_id = template_id;
  profile.source_file = { name, size_bytes: sizeBytes, format: "pptx" };
  const template: MockTemplate = { template_id, name, job_id: nextId("job"), created_at: iso(now()), startedAt: now(), durationMs: ms(9000), profile };
  store.templates.set(template_id, template);
  persistStore();
  return { template, cached: false };
}

export function seedDemoTemplate(): MockTemplate {
  const found = [...store.templates.values()].find((t) => t.name === "Шаблон презентации VK Education.pptx");
  if (found) return found;
  const { template } = createTemplate("Шаблон презентации VK Education.pptx", 23500000);
  template.startedAt = now() - template.durationMs - 1000;
  persistStore();
  return template;
}

export function templateStatus(t: MockTemplate): Status {
  return now() - t.startedAt >= t.durationMs ? "succeeded" : now() - t.startedAt < ms(800) ? "queued" : "running";
}

// ---------- содержание ----------

export function createPackage(fileNames: string[], brief?: Record<string, unknown>): MockPackage {
  const package_id = nextId("pkg");
  const pkg = structuredClone(packageExample) as unknown as ContentPackage;
  pkg.package_id = package_id;
  const mode: MockPackage["mode"] = fileNames.length && brief ? "mixed" : brief ? "brief" : "package";
  pkg.mode = mode;
  if (brief) pkg.brief = { ...pkg.brief, ...(brief as Partial<ContentPackage["brief"]>) };
  if (fileNames.length) {
    pkg.sources = fileNames.map((n, i) => ({ source_id: `src_${i + 1}`, kind: kindByName(n), name: n, extracted: true }));
  }
  const p: MockPackage = { package_id, job_id: nextId("job"), startedAt: now(), durationMs: ms(2500), pkg, fileNames, mode };
  store.packages.set(package_id, p);
  persistStore();
  return p;
}

function kindByName(n: string): ContentPackage["sources"][number]["kind"] {
  const ext = n.split(".").pop()?.toLowerCase();
  const map: Record<string, ContentPackage["sources"][number]["kind"]> = { docx: "docx", xlsx: "xlsx", csv: "csv", pdf: "pdf", md: "markdown", txt: "text", png: "image", jpg: "image", jpeg: "image", json: "json" };
  return map[ext ?? ""] ?? "text";
}

export function packageStatus(p: MockPackage): Status {
  return now() - p.startedAt >= p.durationMs ? "succeeded" : "running";
}

// ---------- генерация ----------

export function createGeneration(req: GenerationRequest): MockGeneration {
  const template = store.templates.get(req.template_id);
  const pkg = store.packages.get(req.package_id);
  if (!template || !pkg) throw new Error("template_or_package_missing");
  const job_id = nextId("job");
  const variantIds = (req.settings?.variants ?? ["compact", "balanced", "detailed"]) as MockVariant["variant_id"][];
  const failDetailed = /fail/i.test(template.name);
  const base = now();
  const story = structuredClone(storyExample) as unknown as StoryPlan;
  story.package_id = pkg.package_id;
  const plan = structuredClone(planExample) as unknown as SlidePlan;
  const variants: MockVariant[] = variantIds.map((variant_id, i) => ({
    variant_id,
    startedAt: base,
    planMs: ms(9000 + i * 1500),
    composeMs: ms(1500),
    exportMs: ms(4000),
    auditMs: ms(7000 + i * 1000),
    fail: failDetailed && variant_id === "detailed",
    revisions: [],
    currentRevision: 1,
    slideCount: variant_id === "compact" ? 10 : variant_id === "balanced" ? 12 : 15,
  }));
  const gen: MockGeneration = { job_id, request: req, template, pkg, createdAt: base, canceled: false, variants, storyMs: ms(4000), story, plan };
  store.generations.set(job_id, gen);
  persistStore();
  return gen;
}

interface VariantTimeline {
  planDone: number;
  composeDone: number;
  exportDone: number;
  auditDone: number;
}

function timeline(g: MockGeneration, v: MockVariant): VariantTimeline {
  const preludeDone = g.createdAt + ms(1200) + g.storyMs; // очередь + смысловой план
  const planDone = preludeDone + v.planMs;
  const composeDone = planDone + v.composeMs;
  const exportDone = composeDone + v.exportMs;
  const auditDone = exportDone + v.auditMs;
  return { planDone, composeDone, exportDone, auditDone };
}

function variantAudit(g: MockGeneration, v: MockVariant): AuditReport {
  const rev = v.revisions[v.revisions.length - 1];
  if (rev) return rev.audit;
  const a = structuredClone(auditExample) as unknown as AuditReport;
  a.job_id = g.job_id;
  a.variant_id = v.variant_id;
  a.report_id = `audit_${g.job_id}_${v.variant_id}_r1`;
  a.deck.slide_count = v.slideCount;
  a.deck.pptx_artifact = `${v.variant_id}/r1/deck.pptx`;
  if (v.variant_id === "compact") {
    a.issues = [];
    a.results = a.results.filter((r) => r.outcome !== "failed").map((r) => ({ ...r, issue_ids: [] }));
    a.coverage = { complete: true, checked: 6, not_checked: 0, not_applicable: 1, missing_inputs: [] };
    a.summary = { issues_total: 0, by_severity: { blocking: 0, error: 0, warning: 0, info: 0 }, by_category: {}, by_kind: {}, slides_with_issues: 0, score: 100 };
    a.contextual_answers = [];
  }
  return a;
}

export function buildAudit(g: MockGeneration, variantId: string, revision?: number): AuditReport | null {
  const v = g.variants.find((x) => x.variant_id === variantId);
  if (!v) return null;
  if (revision && revision !== v.currentRevision) {
    const rec = v.revisions.find((r) => r.revision === revision);
    if (rec) return rec.audit;
    if (revision === 1) return { ...variantAudit(g, { ...v, revisions: [] }), revision: 1 };
    return null;
  }
  const a = variantAudit(g, v);
  return { ...a, revision: v.currentRevision };
}

export function buildResult(g: MockGeneration): GenerationResult {
  const t = now();
  const base = structuredClone(resultExample) as unknown as GenerationResult;
  const queueDone = g.createdAt + ms(1200);
  const storyDone = queueDone + g.storyMs;
  const variants: GenerationResult["variants"] = g.variants.map((v) => {
    const tl = timeline(g, v);
    const pre = `${v.variant_id}/r${v.currentRevision}/`;
    const audit = buildAudit(g, v.variant_id) as AuditReport;
    const thumbs = Array.from({ length: v.slideCount }, (_, i) => ({ slide_index: i, name: `${pre}thumbs/slide-${String(i + 1).padStart(2, "0")}.png`, width_px: 1280, height_px: 720 }));
    let status: GenerationResult["variants"][number]["status"] = "pending";
    if (g.canceled) status = "failed";
    else if (t >= tl.auditDone) status = audit.summary.issues_total > 0 ? "needs_review" : "ready";
    else if (t >= tl.exportDone) status = "ready";
    else if (t >= storyDone) status = "running";
    if (v.fail && t >= tl.planDone + v.composeMs) status = "failed";
    const failed = status === "failed";
    const filesReady = !failed && t >= tl.exportDone;
    const stages = [
      { stage: "plan" as const, variant_id: v.variant_id, status: stageState(t, storyDone, tl.planDone), duration_ms: t >= tl.planDone ? v.planMs : undefined, quota_wait_ms: 1800 },
      { stage: "compose" as const, variant_id: v.variant_id, status: v.fail && t >= tl.planDone + v.composeMs ? ("failed" as const) : stageState(t, tl.planDone, tl.composeDone), duration_ms: t >= tl.composeDone ? v.composeMs : undefined },
      { stage: "export" as const, variant_id: v.variant_id, status: failed ? ("skipped" as const) : stageState(t, tl.composeDone, tl.exportDone), duration_ms: t >= tl.exportDone ? v.exportMs : undefined },
      { stage: "audit" as const, variant_id: v.variant_id, status: failed ? ("skipped" as const) : stageState(t, tl.exportDone, tl.auditDone), duration_ms: t >= tl.auditDone ? v.auditMs : undefined, quota_wait_ms: 4000 },
    ];
    const rationale = { compact: "Минимум текста, один факт на слайд, таблицы заменены графиками", balanced: "Тезис и пояснение, графики с подписями, таблицы до 5 строк", detailed: "Подробные буллеты в пределах порогов, таблицы и схемы, разделители секций" }[v.variant_id];
    return {
      variant_id: v.variant_id,
      axis: "density",
      value: v.variant_id,
      rationale,
      status,
      revision: v.currentRevision,
      revisions: [
        { revision: 1, created_at: iso(tl.exportDone), pptx_hash: "sha256:mock", artifacts_prefix: `${v.variant_id}/r1/` },
        ...v.revisions.map((r) => ({ revision: r.revision, created_at: r.created_at, changed_slide_ids: r.changed_slide_ids, pptx_hash: "sha256:mock", artifacts_prefix: `${v.variant_id}/r${r.revision}/` })),
      ],
      slide_count: v.slideCount,
      plan_artifact: filesReady ? `${pre}plan.json` : undefined,
      composed_deck_artifact: filesReady ? `${pre}composed.json` : undefined,
      ready_at: filesReady ? iso(tl.exportDone) : undefined,
      audited_at: t >= tl.auditDone && !failed ? iso(tl.auditDone) : undefined,
      audit: failed
        ? { status: "failed", coverage_complete: false, issues_total: 0, blocking: 0 }
        : t >= tl.auditDone
          ? { status: audit.coverage.complete ? "complete" : "partial", coverage_complete: audit.coverage.complete, issues_total: audit.summary.issues_total, blocking: audit.summary.by_severity.blocking ?? 0, report_artifact: `${pre}audit.json` }
          : t >= tl.exportDone
            ? { status: "running", coverage_complete: false, issues_total: 0, blocking: 0 }
            : { status: "pending", coverage_complete: false, issues_total: 0, blocking: 0 },
      artifacts: filesReady ? { pptx: `${pre}deck.pptx`, pdf: `${pre}deck.pdf`, html: `${pre}deck.html`, thumbnails: thumbs } : { thumbnails: [] },
      error: failed && !g.canceled ? { code: "compose_failed", message: "Не удалось клонировать образец slide29: битая ссылка на медиа", stage: "compose", retryable: true } : g.canceled ? { code: "canceled", message: "Задание отменено пользователем" } : undefined,
      stages,
    };
  });
  const allDone = variants.every((v) => ["ready", "needs_review", "failed"].includes(v.status));
  const anyFailed = variants.some((v) => v.status === "failed");
  const anyReview = variants.some((v) => v.status === "needs_review" || (v.audit && !v.audit.coverage_complete));
  let status: GenerationResult["status"] = "running";
  let stage: GenerationResult["stage"] = "queued";
  if (g.canceled) {
    status = "canceled";
    stage = "done";
  } else if (t < queueDone) {
    status = "queued";
    stage = "queued";
  } else if (allDone) {
    status = anyFailed || anyReview ? "needs_review" : "succeeded";
    stage = "done";
  } else if (t < storyDone) stage = "story";
  else {
    const running = variants.find((v) => v.status === "running" || v.status === "ready");
    const st = running?.stages?.find((s) => s.status === "running");
    stage = (st?.stage as GenerationResult["stage"]) ?? "plan";
  }
  const manifest: GenerationResult["artifacts_manifest"] = {};
  variants.forEach((v) => {
    const a = v.artifacts;
    if (!a) return;
    if (a.pptx) manifest[a.pptx] = { content_type: "application/vnd.openxmlformats-officedocument.presentationml.presentation", size_bytes: 2400000, sha256: "mock" };
    if (a.pdf) manifest[a.pdf] = { content_type: "application/pdf", size_bytes: 900000, sha256: "mock" };
    if (a.html) manifest[a.html] = { content_type: "text/html", size_bytes: 350000, sha256: "mock" };
    a.thumbnails?.forEach((th) => (manifest[th.name] = { content_type: "image/png", size_bytes: 180000, sha256: "mock" }));
    if (v.plan_artifact) manifest[v.plan_artifact] = { content_type: "application/json", size_bytes: 40000, sha256: "mock" };
    if (v.composed_deck_artifact) manifest[v.composed_deck_artifact] = { content_type: "application/json", size_bytes: 40000, sha256: "mock" };
    if (v.audit?.report_artifact) manifest[v.audit.report_artifact] = { content_type: "application/json", size_bytes: 30000, sha256: "mock" };
  });
  const elapsed = Math.min(t, g.createdAt + Math.max(...g.variants.map((v) => timeline(g, v).auditDone - g.createdAt))) - g.createdAt;
  const doneVariants = variants.filter((v) => v.ready_at);
  return {
    ...base,
    job_id: g.job_id,
    status,
    stage,
    progress: { percent: progressPercent(t, g), message: progressMessage(stage, variants) },
    template_id: g.template.template_id,
    package_id: g.pkg.package_id,
    story_id: g.story.story_id,
    request: g.request,
    created_at: iso(g.createdAt),
    finished_at: allDone ? iso(t) : undefined,
    partial: anyFailed,
    execution_mode: { mode: "stub", layers: { "parsing.template": "stub", "parsing.content": "stub", "generation.story": "stub", "generation.plan": "stub", layout: "stub", export: "stub", "audit.deterministic": "stub", "audit.contextual": "stub" } },
    variants,
    artifacts_manifest: manifest,
    metrics: {
      ...base.metrics,
      stages: [
        { stage: "analyze", status: "done", duration_ms: 9000, cache_hit: true },
        { stage: "import", status: "done", duration_ms: 1200 },
        { stage: "story", status: stageState(t, queueDone, storyDone), duration_ms: t >= storyDone ? g.storyMs : undefined, quota_wait_ms: 500 },
      ],
      queue_wait_ms: 1200,
      totals: { duration_ms: elapsed, llm_calls: Math.min(44, Math.round((elapsed / 60000) * 44)), prompt_tokens: Math.round((elapsed / 60000) * 96000), completion_tokens: Math.round((elapsed / 60000) * 21000) },
      timeline: {
        accepted_at: iso(g.createdAt),
        first_file_ready_ms: doneVariants.length ? Math.min(...doneVariants.map((v) => Date.parse(v.ready_at as string))) - g.createdAt : undefined,
        all_variants_ready_ms: allDone ? Math.max(...g.variants.map((v) => timeline(g, v).exportDone)) - g.createdAt : undefined,
        all_variants_audited_ms: allDone ? Math.max(...g.variants.map((v) => timeline(g, v).auditDone)) - g.createdAt : undefined,
      },
    },
    repairs: [...store.repairs.values()].filter((r) => r.job_id === g.job_id && r.applied).map((r) => ({ repair_job_id: r.repair_job_id, variant_id: r.variant_id, issue_ids: r.issue_ids, result: "applied" as const, base_revision: r.base_revision, new_revision: r.base_revision + 1 })),
    warnings: anyFailed ? [{ code: "variant_failed", message: "Вариант detailed не собран; доступны остальные варианты" }] : [{ code: "font_substituted", message: "Шрифт Arial заменён на Liberation Sans при рендеринге миниатюр" }],
  };
}

function stageState(t: number, start: number, end: number): "pending" | "running" | "done" {
  if (t >= end) return "done";
  if (t >= start) return "running";
  return "pending";
}

function progressPercent(t: number, g: MockGeneration): number {
  const total = Math.max(...g.variants.map((v) => timeline(g, v).auditDone)) - g.createdAt;
  return Math.max(0, Math.min(100, Math.round(((t - g.createdAt) / total) * 100)));
}

function progressMessage(stage: string, variants: GenerationResult["variants"]): string {
  const ready = variants.filter((v) => v.ready_at).length;
  const audited = variants.filter((v) => v.audited_at).length;
  if (stage === "done") return `Готово: ${variants.length} вариантов, аудит завершён у ${audited}`;
  if (stage === "queued") return "Задание в очереди";
  if (stage === "story") return "Строится общий смысловой план";
  return `Вариантов с файлами: ${ready} из ${variants.length}; аудит завершён у ${audited}`;
}

export function buildJobStatus(g: MockGeneration): JobStatus {
  const r = buildResult(g);
  return {
    schema_version: "1.1",
    job_id: g.job_id,
    kind: "generation",
    status: r.status,
    stage: r.stage,
    stages: r.metrics.stages,
    progress: r.progress,
    created_at: r.created_at,
    started_at: iso(g.createdAt + ms(1200)),
    finished_at: r.finished_at,
    queue_wait_ms: 1200,
    depends_on: [g.template.job_id, g.pkg.job_id],
    result: { template_id: g.template.template_id, package_id: g.pkg.package_id, generation_result_url: `/api/generations/${g.job_id}` },
    error: r.error,
  };
}

export function templateJobStatus(t: MockTemplate): JobStatus {
  const status = templateStatus(t);
  return {
    schema_version: "1.1",
    job_id: t.job_id,
    kind: "template_analysis",
    status,
    stage: status === "succeeded" ? "done" : "analyze",
    stages: [{ stage: "analyze", status: status === "succeeded" ? "done" : "running", duration_ms: status === "succeeded" ? t.durationMs : undefined, quota_wait_ms: 2100 }],
    progress: { percent: status === "succeeded" ? 100 : Math.round(((now() - t.startedAt) / t.durationMs) * 100), message: status === "succeeded" ? `Профиль готов: ${t.profile.patterns.length} паттернов` : "Анализ образцов шаблона" },
    created_at: t.created_at,
    finished_at: status === "succeeded" ? iso(t.startedAt + t.durationMs) : undefined,
    result: { template_id: t.template_id, template_profile_url: `/api/templates/${t.template_id}` },
  };
}

export function packageJobStatus(p: MockPackage): JobStatus {
  const status = packageStatus(p);
  const pk = p.pkg;
  return {
    schema_version: "1.1",
    job_id: p.job_id,
    kind: "content_import",
    status,
    stage: status === "succeeded" ? "done" : "import",
    stages: [{ stage: "import", status: status === "succeeded" ? "done" : "running", duration_ms: status === "succeeded" ? p.durationMs : undefined }],
    progress: { percent: status === "succeeded" ? 100 : 50, message: status === "succeeded" ? `Импорт готов: ${pk.blocks.length} блоков, ${pk.facts.length} фактов, ${pk.datasets.length} таблиц, ${pk.assets.length} изображений` : "Извлечение блоков и фактов" },
    created_at: iso(p.startedAt),
    finished_at: status === "succeeded" ? iso(p.startedAt + p.durationMs) : undefined,
    result: { package_id: p.package_id, content_package_url: `/api/content/${p.package_id}` },
  };
}

// ---------- исправления ----------

export function createRepair(g: MockGeneration, variantId: string, baseRevision: number, issueIds: string[]): MockRepair | { conflict: number } {
  const v = g.variants.find((x) => x.variant_id === variantId);
  if (!v) throw new Error("variant_missing");
  if (baseRevision !== v.currentRevision) return { conflict: v.currentRevision };
  const repair: MockRepair = { repair_job_id: nextId("rep"), job_id: g.job_id, variant_id: variantId, base_revision: baseRevision, issue_ids: issueIds, startedAt: now(), durationMs: ms(5000), applied: false };
  store.repairs.set(repair.repair_job_id, repair);
  persistStore();
  return repair;
}

/** Применяет завершившиеся исправления: создаёт новую ревизию с обновлённым отчётом. */
export function settleRepairs(): void {
  for (const r of store.repairs.values()) {
    if (r.applied || now() - r.startedAt < r.durationMs) continue;
    const g = store.generations.get(r.job_id);
    const v = g?.variants.find((x) => x.variant_id === r.variant_id);
    if (!g || !v) continue;
    const prev = buildAudit(g, v.variant_id) as AuditReport;
    const fixed = new Set(r.issue_ids);
    const newRev = v.currentRevision + 1;
    const changed = [...new Set(prev.issues.filter((i) => fixed.has(i.issue_id)).map((i) => `s${i.slide_index + 1}`))];
    const audit: AuditReport = {
      ...structuredClone(prev),
      report_id: `audit_${g.job_id}_${v.variant_id}_r${newRev}`,
      revision: newRev,
      created_at: iso(now()),
      issues: prev.issues.filter((i) => !fixed.has(i.issue_id)).map((i) => ({ ...i, revision: newRev })),
      results: prev.results.map((res) => (res.issue_ids?.some((id) => fixed.has(id)) ? { ...res, outcome: "passed" as const, issue_ids: [] } : res)),
      rechecked_after_repair: { base_revision: v.currentRevision, changed_slide_ids: changed, dependent_slide_ids: [], deck_checks_rerun: ["integrity.duplicate_slides"] },
    };
    const remaining = audit.issues.length;
    audit.summary = { ...audit.summary, issues_total: remaining, by_severity: { blocking: 0, error: audit.issues.filter((i) => i.severity === "error").length, warning: audit.issues.filter((i) => i.severity === "warning").length, info: 0 }, slides_with_issues: new Set(audit.issues.map((i) => i.slide_index)).size, score: Math.min(100, (audit.summary.score ?? 88) + 6) };
    v.revisions.push({ revision: newRev, created_at: audit.created_at, changed_slide_ids: changed, audit, fixedIssueIds: r.issue_ids });
    v.currentRevision = newRev;
    r.applied = true;
    persistStore();
  }
}

export function repairJobStatus(r: MockRepair): JobStatus {
  const done = r.applied || now() - r.startedAt >= r.durationMs;
  return {
    schema_version: "1.1",
    job_id: r.repair_job_id,
    kind: "repair",
    status: done ? "succeeded" : "running",
    stage: done ? "done" : "repair",
    stages: [{ stage: "repair", status: done ? "done" : "running", duration_ms: done ? r.durationMs : undefined }],
    progress: { percent: done ? 100 : Math.round(((now() - r.startedAt) / r.durationMs) * 100), message: done ? "Исправления применены, затронутые слайды перепроверены" : "Исправление выбранных находок" },
    created_at: iso(r.startedAt),
    finished_at: done ? iso(r.startedAt + r.durationMs) : undefined,
    parent_job_id: r.job_id,
    result: { revision: r.base_revision + 1, generation_result_url: `/api/generations/${r.job_id}` },
  };
}
