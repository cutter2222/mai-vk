import { HttpResponse, delay, http } from "msw";

import { API_BASE } from "@/lib/api/config";
import type { GenerationRequest } from "@/lib/api/types";

import { htmlBlob, pdfBlob, pptxBlob, slidePng } from "./files";
import {
  buildAudit,
  buildJobStatus,
  buildResult,
  createGeneration,
  createPackage,
  createRepair,
  createTemplate,
  packageJobStatus,
  packageStatus,
  persistStore,
  repairJobStatus,
  seedDemoTemplate,
  settleRepairs,
  store,
  templateJobStatus,
  templateStatus,
} from "./state";

const base = (path: string) => `${API_BASE}${path}`;

interface FileMeta {
  name: string;
  size: number;
}

/**
 * Файлы из multipart. Safari передаёт в service worker тело без бинарных частей,
 * поэтому клиент в режиме заглушек дублирует имена и размеры заголовком X-Mock-Files.
 */
function filesFrom(form: FormData, request: Request, field: string): FileMeta[] {
  const fromForm = form.getAll(field).filter((v): v is File => typeof v === "object" && v !== null && "name" in v && "size" in v).map((f) => ({ name: f.name, size: f.size }));
  if (fromForm.length) return fromForm;
  const header = request.headers.get("X-Mock-Files");
  if (!header) return [];
  try {
    return JSON.parse(decodeURIComponent(header)) as FileMeta[];
  } catch {
    return [];
  }
}
const err = (status: number, code: string, message: string, details?: Record<string, unknown>) =>
  HttpResponse.json({ error: { code, message, details } }, { status });

const SLIDE_TITLES = [
  "Умные уведомления: пилот и план запуска",
  "Проблема",
  "Пользователи теряют почти половину важных уведомлений",
  "Решение",
  "Три механизма возвращают внимание пользователей",
  "Как работает приоритизация: четыре шага",
  "Открываемость выросла с 31 до 44 % за три месяца пилота",
  "Метрики пилота по месяцам",
  "Пилот окупается за год",
  "Просим одобрить расширение пилота",
  "Риски и как мы их снимаем",
  "План на четвёртый квартал",
  "Команда пилота",
  "Что нужно от руководителей",
  "Приложение: методика измерений",
];

export const handlers = [
  http.get(base("/health"), () =>
    HttpResponse.json({ status: "ok", workers: { analysis: 1, generation: 3 }, valkey_ok: true, renderer_ok: true, version: "0.1.0-mock" }),
  ),

  http.get(base("/capabilities"), () =>
    HttpResponse.json({
      contracts_version: "1.1",
      execution_mode: { mode: "stub", layers: { "parsing.template": "stub", "parsing.content": "stub", "generation.story": "stub", "generation.plan": "stub", layout: "stub", export: "stub", "audit.deterministic": "stub", "audit.contextual": "stub" } },
      features: { generate_images: false, contextual_audit: true, html_export: true },
      limits: { max_upload_mb: 100, max_content_files: 20, slide_count_max: 60 },
    }),
  ),

  // ---------- шаблоны ----------
  http.get(base("/templates"), () => {
    seedDemoTemplate();
    return HttpResponse.json(
      [...store.templates.values()].map((t) => ({ template_id: t.template_id, name: t.name, status: templateStatus(t), slide_count: t.profile.stats.slides, created_at: t.created_at })),
    );
  }),

  http.post(base("/templates"), async ({ request }) => {
    const form = await request.formData();
    const [file] = filesFrom(form, request, "file");
    if (!file) return err(400, "file_required", "Не передан файл шаблона");
    if (!file.name.toLowerCase().endsWith(".pptx")) return err(415, "unsupported_format", "Поддерживается только формат PPTX");
    if (file.size > 100 * 1024 * 1024) return err(413, "file_too_large", "Файл больше 100 МБ");
    await delay(400);
    const { template, cached } = createTemplate(file.name, file.size);
    return HttpResponse.json({ template_id: template.template_id, job_id: template.job_id, cached }, { status: 202 });
  }),

  http.get(base("/templates/:id"), ({ params }) => {
    const t = store.templates.get(String(params.id));
    if (!t) return err(404, "template_not_found", "Шаблон не найден");
    const status = templateStatus(t);
    const previews = status === "succeeded" ? t.profile.patterns.map((p) => p.preview_path ?? "").filter(Boolean) : [];
    return HttpResponse.json({ status, job_id: t.job_id, name: t.name, profile: status === "succeeded" ? t.profile : undefined, previews });
  }),

  http.get(base("/templates/:id/assets/*"), async ({ params, request }) => {
    const t = store.templates.get(String(params.id));
    if (!t) return err(404, "template_not_found", "Шаблон не найден");
    const name = new URL(request.url).pathname.split("/assets/")[1] ?? "";
    const pattern = t.profile.patterns.find((p) => p.preview_path === name);
    const png = await slidePng(`tpl:${t.template_id}:${name}`, pattern?.name ?? "Образец", `Образец шаблона: ${pattern?.role ?? ""}`, "#0077FF", 640);
    return new HttpResponse(png, { headers: { "Content-Type": "image/png" } });
  }),

  // ---------- содержание ----------
  http.post(base("/content"), async ({ request }) => {
    const form = await request.formData();
    const files = filesFrom(form, request, "files");
    const briefRaw = form.get("brief");
    const brief = typeof briefRaw === "string" && briefRaw ? (JSON.parse(briefRaw) as Record<string, unknown>) : undefined;
    if (!files.length && !brief) return err(400, "content_required", "Добавьте файлы контент-пакета или заполните бриф");
    if (files.length > 20) return err(400, "too_many_files", "Не больше 20 файлов");
    await delay(300);
    const p = createPackage(files.map((f) => f.name), brief);
    return HttpResponse.json({ package_id: p.package_id, job_id: p.job_id }, { status: 202 });
  }),

  http.get(base("/content/:id"), ({ params }) => {
    const p = store.packages.get(String(params.id));
    if (!p) return err(404, "package_not_found", "Контент-пакет не найден");
    const status = packageStatus(p);
    return HttpResponse.json({ status, job_id: p.job_id, package: status === "succeeded" ? p.pkg : undefined });
  }),

  // ---------- генерация ----------
  http.post(base("/generations"), async ({ request }) => {
    const body = (await request.json()) as GenerationRequest;
    if (!store.templates.has(body.template_id)) return err(404, "template_not_found", "Шаблон не найден");
    if (!store.packages.has(body.package_id)) return err(404, "package_not_found", "Контент-пакет не найден");
    const sc = body.settings?.slide_count;
    if (sc?.min != null && sc?.max != null && sc.min > sc.max) return err(422, "slide_count_range", "Минимум слайдов больше максимума");
    if (body.idempotency_key) {
      const same = [...store.generations.values()].find((g) => g.request.idempotency_key === body.idempotency_key);
      if (same) return HttpResponse.json({ job_id: same.job_id }, { status: 202 });
    }
    await delay(300);
    const g = createGeneration(body);
    return HttpResponse.json({ job_id: g.job_id }, { status: 202 });
  }),

  http.get(base("/generations/:jobId"), ({ params }) => {
    settleRepairs();
    const g = store.generations.get(String(params.jobId));
    if (!g) return err(404, "job_not_found", "Задание не найдено. Проверьте ссылку или начните новую генерацию.");
    return HttpResponse.json(buildResult(g));
  }),

  http.get(base("/generations/:jobId/story"), ({ params }) => {
    const g = store.generations.get(String(params.jobId));
    if (!g) return err(404, "job_not_found", "Задание не найдено");
    return HttpResponse.json(g.story);
  }),

  http.get(base("/generations/:jobId/variants/:variantId/audit"), ({ params, request }) => {
    settleRepairs();
    const g = store.generations.get(String(params.jobId));
    if (!g) return err(404, "job_not_found", "Задание не найдено");
    const revParam = new URL(request.url).searchParams.get("revision");
    const audit = buildAudit(g, String(params.variantId), revParam ? Number(revParam) : undefined);
    if (!audit) return err(404, "audit_not_found", "Отчёт аудита для этой ревизии не найден");
    return HttpResponse.json(audit);
  }),

  http.post(base("/generations/:jobId/variants/:variantId/repairs"), async ({ params, request }) => {
    settleRepairs();
    const g = store.generations.get(String(params.jobId));
    if (!g) return err(404, "job_not_found", "Задание не найдено");
    const body = (await request.json()) as { base_revision: number; issue_ids: string[] };
    if (!body.issue_ids?.length) return err(422, "issues_required", "Выберите хотя бы одну находку");
    const result = createRepair(g, String(params.variantId), body.base_revision, body.issue_ids);
    if ("conflict" in result) return err(409, "revision_stale", `Ревизия ${body.base_revision} устарела: текущая ревизия ${result.conflict}. Обновите отчёт и выберите находки заново.`, { current_revision: result.conflict });
    await delay(200);
    return HttpResponse.json({ repair_job_id: result.repair_job_id }, { status: 202 });
  }),

  http.get(base("/generations/:jobId/artifacts/*"), async ({ params, request }) => {
    settleRepairs();
    const g = store.generations.get(String(params.jobId));
    if (!g) return err(404, "job_not_found", "Задание не найдено");
    const name = new URL(request.url).pathname.split("/artifacts/")[1] ?? "";
    const result = buildResult(g);
    if (!result.artifacts_manifest?.[name]) return err(404, "artifact_not_found", "Артефакт не найден в манифесте задания");
    const [variantId, rev] = name.split("/");
    const variant = g.variants.find((v) => v.variant_id === variantId);
    const revision = Number(rev.replace("r", ""));
    const audit = buildAudit(g, variantId, revision);
    const title = `${g.story.theses[0]?.statement ?? "Презентация"}`;
    const dispo = (fname: string) => `attachment; filename="${fname}"; filename*=UTF-8''${encodeURIComponent(fname)}`;
    if (name.endsWith("/deck.pptx")) return new HttpResponse(pptxBlob(`${title} (${variantId}, ревизия ${revision})`), { headers: { "Content-Type": "application/vnd.openxmlformats-officedocument.presentationml.presentation", "Content-Disposition": dispo(`${variantId}-r${revision}.pptx`) } });
    if (name.endsWith("/deck.pdf")) return new HttpResponse(pdfBlob(`${variantId} r${revision}`), { headers: { "Content-Type": "application/pdf", "Content-Disposition": dispo(`${variantId}-r${revision}.pdf`) } });
    if (name.endsWith("/deck.html")) return new HttpResponse(htmlBlob(title, SLIDE_TITLES.slice(0, variant?.slideCount ?? 10)), { headers: { "Content-Type": "text/html", "Content-Disposition": dispo(`${variantId}-r${revision}.html`) } });
    if (name.includes("/thumbs/")) {
      const idx = Number(name.match(/slide-(\d+)\.png/)?.[1] ?? 1) - 1;
      const hasIssue = audit?.issues.some((i) => i.slide_index === idx) ?? false;
      const fixedMark = revision > 1 && (variant?.revisions.some((r) => r.revision <= revision && r.changed_slide_ids.includes(`s${idx + 1}`)) ?? false);
      const subtitle = hasIssue ? "На этом слайде есть находки аудита" : fixedMark ? "Слайд исправлен в новой ревизии" : `Вариант ${variantId}, ревизия ${revision}`;
      const png = await slidePng(`${g.job_id}:${name}:${hasIssue}`, SLIDE_TITLES[idx] ?? `Слайд ${idx + 1}`, subtitle, variantId === "compact" ? "#0077FF" : variantId === "balanced" ? "#FF3885" : "#520977");
      return new HttpResponse(png, { headers: { "Content-Type": "image/png" } });
    }
    if (name.endsWith("plan.json")) return HttpResponse.json(g.plan);
    if (name.endsWith("audit.json")) return HttpResponse.json(audit);
    return HttpResponse.json({ mock: true, name });
  }),

  // ---------- задания ----------
  http.get(base("/jobs/:id"), ({ params }) => {
    settleRepairs();
    const id = String(params.id);
    const g = store.generations.get(id);
    if (g) return HttpResponse.json(buildJobStatus(g));
    const t = [...store.templates.values()].find((x) => x.job_id === id);
    if (t) return HttpResponse.json(templateJobStatus(t));
    const p = [...store.packages.values()].find((x) => x.job_id === id);
    if (p) return HttpResponse.json(packageJobStatus(p));
    const r = store.repairs.get(id);
    if (r) return HttpResponse.json(repairJobStatus(r));
    return err(404, "job_not_found", "Задание не найдено");
  }),

  http.post(base("/jobs/:id/cancel"), ({ params }) => {
    const g = store.generations.get(String(params.id));
    if (!g) return err(404, "job_not_found", "Задание не найдено");
    g.canceled = true;
    persistStore();
    return new HttpResponse(null, { status: 202 });
  }),

  http.post(base("/jobs/:id/retry"), async ({ params }) => {
    const g = store.generations.get(String(params.id));
    if (!g) return err(404, "job_not_found", "Задание не найдено");
    await delay(200);
    const fresh = createGeneration({ ...g.request, idempotency_key: undefined });
    fresh.variants.forEach((v) => (v.fail = false));
    persistStore();
    return HttpResponse.json({ job_id: fresh.job_id }, { status: 202 });
  }),
];
