import { HttpResponse, delay, http } from "msw";

import { API_BASE } from "@/lib/api/config";
import type { Event, GenerationRequest, Override, ProjectFile } from "@/lib/api/types";

import { extractBrief } from "./brief";
import { buildDeck } from "./deck";
import { assetPng, htmlBlob, pdfBlob, pptxBlob, slidePng } from "./files";
import * as projects from "./projects";
import {
  buildAudit,
  buildJobStatus,
  buildResult,
  createEdit,
  createGeneration,
  createPackage,
  createPatch,
  createRepair,
  createTemplate,
  deleteTemplate,
  packageJobStatus,
  packageStatus,
  persistStore,
  repairJobStatus,
  seedDemoTemplate,
  settleRepairs,
  slideTitleAt,
  store,
  templateJobStatus,
  templateStatus,
} from "./state";

const base = (path: string) => `${API_BASE}${path}`;

/** Сколько «едет» файл проекта; тесты растягивают загрузку через mock_upload_ms, чтобы проверить экран в это время. */
const UPLOAD_MS = Number(typeof window !== "undefined" ? window.localStorage.getItem("mock_upload_ms") ?? "150" : "150") || 150;
const SPEECH = typeof window === "undefined" || window.localStorage.getItem("mock_speech") !== "off";
const SPEECH_MS = Number(typeof window !== "undefined" ? window.localStorage.getItem("mock_speech_ms") ?? "300" : "300") || 300;
const SPEECH_PHRASES = ["Сделай заголовок короче.", "Добавь вывод на последний слайд.", "Проверка голосового ввода."];
let speechCalls = 0;

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

/** Цвет картинки-заглушки по sha256: разные файлы в сетке различимы. */
const MOCK_COLORS = ["#0077FF", "#FF3885", "#2DB86A", "#F5A623", "#7B61FF"];
const mockColor = (sha: string) => MOCK_COLORS[parseInt(sha.slice(0, 2), 16) % MOCK_COLORS.length];

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
  http.get(base("/office/capabilities"), () => HttpResponse.json({ enabled: false })),
  http.get(base("/health"), () =>
    HttpResponse.json({ status: "ok", workers: { analysis: 1, generation: 3 }, valkey_ok: true, renderer_ok: true, version: "0.1.0-mock" }),
  ),

  http.get(base("/capabilities"), () =>
    HttpResponse.json({
      contracts_version: "1.9",
      execution_mode: { mode: "stub", layers: { "parsing.template": "stub", "parsing.content": "stub", brief: "stub", "generation.story": "stub", "generation.plan": "stub", layout: "stub", export: "stub", "audit.deterministic": "stub", "audit.contextual": "stub" } },
      // Голосовой ввод в заглушке есть всегда, кроме mock_speech=off (проверка «кнопки нет»).
      features: { generate_images: false, contextual_audit: true, html_export: true, speech: SPEECH },
      speech: { language: "ru", max_seconds: 25 },
      limits: { max_upload_mb: 100, max_content_files: 20, slide_count_max: 60, max_project_files: 50, max_project_mb: 1024 },
    }),
  ),

  // ---------- голосовой ввод ----------
  // Модели в заглушке нет: каждая фраза «распознаётся» очередной строкой из списка, черновик
  // недоговорённой фразы (?partial=1) — её первой половиной с оборванным следующим словом,
  // как у настоящей модели; очередь фраз черновик не двигает.
  http.post(base("/speech/transcribe"), async ({ request }) => {
    await delay(SPEECH_MS);
    // Прогрев модели при включении микрофона — полсекунды тишины: фразой не считается.
    const audio = (await request.formData()).get("audio");
    if (audio instanceof File && audio.size < 20000) return HttpResponse.json({ text: "", duration_ms: 500, infer_ms: SPEECH_MS, model: "stub" });
    const phrase = SPEECH_PHRASES[speechCalls % SPEECH_PHRASES.length];
    if (new URL(request.url).searchParams.get("partial")) {
      const words = phrase.split(" ");
      const half = Math.ceil(words.length / 2);
      const cut = words[half] ? ` ${words[half].slice(0, 3)}.` : ".";
      return HttpResponse.json({ text: words.slice(0, half).join(" ") + cut, duration_ms: 800, infer_ms: SPEECH_MS, model: "stub" });
    }
    speechCalls += 1;
    return HttpResponse.json({ text: phrase, duration_ms: 1500, infer_ms: SPEECH_MS, model: "stub" });
  }),

  // ---------- проекты ----------
  http.get(base("/projects"), () => {
    settleRepairs();
    return HttpResponse.json(
      projects.projectStore.projects.map((p) => {
        const g = p.job_id ? store.generations.get(p.job_id) : undefined;
        const result = g ? buildResult(g) : null;
        const variant = result?.variants.find((v) => v.variant_id === p.chosen_variant && v.artifacts?.thumbnails?.length) ?? result?.variants.find((v) => v.artifacts?.thumbnails?.length);
        const thumb = variant?.artifacts?.thumbnails?.[0];
        const template = p.template_id ? store.templates.get(p.template_id) : undefined;
        const preview = template?.profile.patterns.find((x) => x.preview_path)?.preview_path;
        return projects.listItem(p, {
          job_status: result?.status ?? null,
          thumbnail_url: thumb && p.job_id ? `/api/generations/${p.job_id}/artifacts/${thumb.name}` : template && preview ? `/api/templates/${template.template_id}/assets/${preview}` : null,
          slide_count: variant?.slide_count ?? null,
          template_name: template?.name ?? null,
        });
      }),
    );
  }),

  http.post(base("/projects"), async ({ request }) => {
    const body = (await request.json().catch(() => ({}))) as { title?: string; job_id?: string };
    return HttpResponse.json(projects.createProject(body), { status: 201 });
  }),

  http.get(base("/projects/:id"), ({ params }) => {
    const p = projects.getProject(String(params.id));
    return p ? HttpResponse.json(p) : err(404, "project_not_found", "Проект не найден");
  }),

  http.patch(base("/projects/:id"), async ({ params, request }) => {
    const patch = (await request.json()) as Record<string, unknown>;
    const p = projects.patchProject(String(params.id), patch);
    return p ? HttpResponse.json(p) : err(404, "project_not_found", "Проект не найден");
  }),

  http.delete(base("/projects/:id"), ({ params }) => (projects.deleteProject(String(params.id)) ? new HttpResponse(null, { status: 204 }) : err(404, "project_not_found", "Проект не найден"))),

  http.get(base("/projects/:id/events"), ({ params }) => {
    const p = projects.getProject(String(params.id));
    return p ? HttpResponse.json(p.events) : err(404, "project_not_found", "Проект не найден");
  }),

  http.post(base("/projects/:id/events"), async ({ params, request }) => {
    const payload = (await request.json()) as Omit<Event, "event_id" | "at">;
    const event = projects.appendEvent(String(params.id), payload);
    return event ? HttpResponse.json(event, { status: 201 }) : err(404, "project_not_found", "Проект не найден");
  }),

  http.patch(base("/projects/:id/events/:eventId"), async ({ params, request }) => {
    const patch = (await request.json()) as Partial<Event>;
    const event = projects.patchEvent(String(params.id), String(params.eventId), patch);
    return event ? HttpResponse.json(event) : err(404, "event_not_found", "Событие не найдено");
  }),

  http.post(base("/projects/:id/files"), async ({ params, request }) => {
    const form = await request.formData();
    const files = filesFrom(form, request, "files");
    if (!files.length) return err(400, "file_required", "Не переданы файлы");
    const rejected = files.find((f) => /\.pptx$/i.test(f.name) && f.size < 2);
    if (rejected) return err(422, "file_rejected", `Файл ${rejected.name} не является PPTX`);
    await delay(UPLOAD_MS);
    const rows = files.map((f) => projects.addFile(String(params.id), f.name, f.size)).filter((r): r is ProjectFile => Boolean(r));
    return rows.length ? HttpResponse.json(rows, { status: 201 }) : err(404, "project_not_found", "Проект не найден");
  }),

  http.patch(base("/projects/:id/files/:fileId"), async ({ params, request }) => {
    const patch = (await request.json()) as Partial<ProjectFile>;
    const file = projects.patchFile(String(params.id), String(params.fileId), patch);
    return file ? HttpResponse.json(file) : err(404, "file_not_found", "Файл не найден");
  }),

  http.delete(base("/projects/:id/files/:fileId"), ({ params }) => (projects.deleteFile(String(params.id), String(params.fileId)) ? new HttpResponse(null, { status: 204 }) : err(404, "file_not_found", "Файл не найден"))),

  // Байты файла заглушка не хранит: картинка рисуется по имени, остальное — файлом-подобием.
  http.get(base("/projects/:id/files/:fileId/content"), async ({ params }) => {
    const found = projects.findFile(String(params.fileId));
    if (!found || found.project.project_id !== String(params.id)) return err(404, "file_not_found", "Файл не найден");
    const { file } = found;
    const format = file.check.format;
    if (format === "image") return new HttpResponse(await assetPng(`file:${file.sha256}`, mockColor(file.sha256), false, 480), { headers: { "Content-Type": "image/png" } });
    if (format === "pdf") return new HttpResponse(pdfBlob(file.name), { headers: { "Content-Type": "application/pdf" } });
    if (format === "pptx") return new HttpResponse(pptxBlob(file.name), { headers: { "Content-Type": "application/vnd.openxmlformats-officedocument.presentationml.presentation" } });
    return new HttpResponse(file.name, { headers: { "Content-Type": "application/octet-stream" } });
  }),

  http.get(base("/projects/:id/files/:fileId/thumbnail"), async ({ params }) => {
    const found = projects.findFile(String(params.fileId));
    if (!found || found.project.project_id !== String(params.id)) return err(404, "file_not_found", "Файл не найден");
    const { file } = found;
    const format = file.check.format;
    if (format === "image") return new HttpResponse(await assetPng(`file:${file.sha256}`, mockColor(file.sha256), false, 480), { headers: { "Content-Type": "image/png" } });
    if (format === "pdf" || format === "pptx") return new HttpResponse(await slidePng(`file-cover:${file.sha256}`, file.name, format === "pdf" ? "Первая страница документа" : "Обложка презентации", "#0077FF", 480), { headers: { "Content-Type": "image/png" } });
    return err(404, "thumbnail_unavailable", "Для этого файла миниатюры нет");
  }),

  // ---------- шаблоны ----------
  http.get(base("/templates"), () => {
    seedDemoTemplate();
    return HttpResponse.json(
      [...store.templates.values()].map((t) => {
        const status = templateStatus(t);
        const preview = status === "succeeded" ? t.profile.patterns.find((p) => p.preview_path)?.preview_path : undefined;
        const colors = Array.from(new Set(t.profile.design_tokens.colors.palette.map((c) => c.hex))).slice(0, 5);
        return { template_id: t.template_id, name: t.name, status, slide_count: t.profile.stats.slides, pattern_count: t.profile.patterns.filter((p) => p.source.kind !== "builtin").length, ...(preview ? { preview } : {}), ...(colors.length ? { colors } : {}), created_at: t.created_at };
      }),
    );
  }),

  http.delete(base("/templates/:id"), ({ params }) => {
    if (!deleteTemplate(String(params.id))) return err(404, "template_not_found", "Шаблон не найден");
    projects.detachTemplate(String(params.id));
    return new HttpResponse(null, { status: 204 });
  }),

  http.post(base("/templates"), async ({ request }) => {
    if (request.headers.get("content-type")?.startsWith("multipart/form-data")) {
      const file = (await request.formData()).get("file");
      if (!(file instanceof File)) return err(400, "file_required", "Выберите PPTX");
      if (!/\.pptx$/i.test(file.name)) return err(415, "unsupported_format", "Поддерживается только PPTX");
      await delay(400);
      const { template, cached } = createTemplate(file.name, file.size);
      return HttpResponse.json({ template_id: template.template_id, job_id: template.job_id, cached }, { status: 202 });
    }
    const body = (await request.json()) as { file_id?: string };
    const found = body.file_id ? projects.findFile(body.file_id) : undefined;
    if (!found) return err(404, "file_not_found", "Файл не найден");
    if (found.file.check.format !== "pptx") return err(415, "unsupported_format", "Шаблоном может быть только PPTX");
    await delay(400);
    const { template, cached } = createTemplate(found.file.name, found.file.size_bytes);
    projects.patchFile(found.project.project_id, found.file.file_id, { kind: "template", template_id: template.template_id });
    return HttpResponse.json({ template_id: template.template_id, job_id: template.job_id, cached }, { status: 202 });
  }),

  http.get(base("/templates/:id"), ({ params }) => {
    const t = store.templates.get(String(params.id));
    if (!t) return err(404, "template_not_found", "Шаблон не найден");
    const status = templateStatus(t);
    const previews = status === "succeeded" ? [...t.profile.patterns.map((p) => p.preview_path ?? "").filter(Boolean), ...t.profile.layouts.map((l) => `previews/layout-${l.layout_id}.png`)] : [];
    return HttpResponse.json({ status, job_id: t.job_id, name: t.name, profile: status === "succeeded" ? t.profile : undefined, previews });
  }),

  http.get(base("/templates/:id/source"), ({ params }) => {
    const template = store.templates.get(String(params.id));
    if (!template) return err(404, "template_not_found", "Шаблон не найден");
    return new HttpResponse(pptxBlob(template.name), { headers: {
      "Content-Type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      "Content-Disposition": `attachment; filename="template.pptx"; filename*=UTF-8''${encodeURIComponent(template.name)}`,
    } });
  }),

  // Байты ресурса шаблона (иконка, логотип, картинка) для панели и холста редактора.
  http.get(base("/templates/:id/media/:assetId"), async ({ params }) => {
    const t = store.templates.get(String(params.id));
    if (!t) return err(404, "template_not_found", "Шаблон не найден");
    const asset = t.profile.assets.find((a) => a.asset_id === String(params.assetId));
    if (!asset) return err(404, "asset_not_found", "Ресурс не найден в профиле шаблона");
    const png = await assetPng(`tpl-media:${asset.asset_id}`, asset.kind === "icon" ? "#0077FF" : "#FF3885", asset.kind === "icon" || asset.kind === "logo");
    return new HttpResponse(png, { headers: { "Content-Type": "image/png", "Cache-Control": "public, max-age=86400" } });
  }),

  http.get(base("/templates/:id/media/:assetId/thumbnail"), async ({ params }) => {
    const t = store.templates.get(String(params.id));
    const asset = t?.profile.assets.find((a) => a.asset_id === String(params.assetId));
    if (!asset) return err(404, "asset_not_found", "Ресурс не найден в профиле шаблона");
    const png = await assetPng(`tpl-media:${asset.asset_id}`, asset.kind === "icon" ? "#0077FF" : "#FF3885", asset.kind === "icon" || asset.kind === "logo");
    return new HttpResponse(png, { headers: { "Content-Type": "image/png" } });
  }),

  http.get(base("/templates/:id/assets/*"), async ({ params, request }) => {
    const t = store.templates.get(String(params.id));
    if (!t) return err(404, "template_not_found", "Шаблон не найден");
    const name = new URL(request.url).pathname.split("/assets/")[1] ?? "";
    if (name.startsWith("previews/layout-")) {
      const layoutId = name.slice("previews/layout-".length).replace(/\.png$/, "");
      const layout = t.profile.layouts.find((l) => l.layout_id === layoutId);
      const png = await slidePng(`tpl-layout:${t.template_id}:${layoutId}`, "", layout?.name ?? layoutId, "#C8CDD7", 640);
      return new HttpResponse(png, { headers: { "Content-Type": "image/png" } });
    }
    const pattern = t.profile.patterns.find((p) => p.preview_path === name);
    const png = await slidePng(`tpl:${t.template_id}:${name}`, pattern?.name ?? "Образец", `Образец шаблона: ${pattern?.role ?? ""}`, "#0077FF", 640);
    return new HttpResponse(png, { headers: { "Content-Type": "image/png" } });
  }),

  // ---------- бриф из сообщения ----------
  http.post(base("/brief"), async ({ request }) => {
    const body = (await request.json()) as { text?: string };
    await delay(350);
    return HttpResponse.json(extractBrief(body.text ?? ""));
  }),

  // ---------- содержание ----------
  http.post(base("/content"), async ({ request }) => {
    const body = (await request.json()) as { file_ids?: string[]; brief?: Record<string, unknown> };
    const found = (body.file_ids ?? []).map((id) => projects.findFile(id)).filter((x): x is NonNullable<typeof x> => Boolean(x));
    const brief = body.brief && Object.values(body.brief).some((v) => v !== "" && v != null && !(Array.isArray(v) && v.length === 0)) ? body.brief : undefined;
    if (!found.length && !brief) return err(400, "content_required", "Добавьте файлы контент-пакета или заполните бриф");
    if (found.length > 20) return err(400, "too_many_files", "Не больше 20 файлов");
    await delay(300);
    const p = createPackage(found.map((f) => f.file.name), brief);
    found.forEach((f) => projects.patchFile(f.project.project_id, f.file.file_id, { package_id: p.package_id }));
    return HttpResponse.json({ package_id: p.package_id, job_id: p.job_id, cached: false }, { status: 202 });
  }),

  http.get(base("/content/:id"), ({ params }) => {
    const p = store.packages.get(String(params.id));
    if (!p) return err(404, "package_not_found", "Контент-пакет не найден");
    const status = packageStatus(p);
    return HttpResponse.json({ status, job_id: p.job_id, package: status === "succeeded" ? p.pkg : undefined });
  }),

  // Картинка контент-пакета по пути ресурса — для панели редактора.
  http.get(base("/content/:id/assets/*"), async ({ params, request }) => {
    const p = store.packages.get(String(params.id));
    if (!p) return err(404, "package_not_found", "Контент-пакет не найден");
    const name = new URL(request.url).pathname.split("/assets/")[1] ?? "";
    const png = await assetPng(`pkg-asset:${name}`, "#520977");
    return new HttpResponse(png, { headers: { "Content-Type": "image/png" } });
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

  http.post(base("/generations/:jobId/variants/:variantId/edits"), async ({ params, request }) => {
    settleRepairs();
    const g = store.generations.get(String(params.jobId));
    if (!g) return err(404, "job_not_found", "Задание не найдено");
    const variant = g.variants.find((v) => v.variant_id === String(params.variantId));
    if (!variant) return err(404, "variant_not_found", "Вариант не найден");
    const body = (await request.json()) as { base_revision: number; slide_index: number; instruction?: string };
    const instruction = (body.instruction ?? "").trim().replace(/\s+/g, " ");
    if (!instruction) return err(422, "instruction_required", "Напишите, что изменить на слайде");
    if (body.slide_index < 0 || body.slide_index >= variant.slideCount) return err(422, "slide_index_out_of_range", `В варианте ${variant.slideCount} слайдов, слайда с номером ${body.slide_index + 1} нет`);
    const result = createEdit(g, variant.variant_id, body.base_revision, body.slide_index, instruction);
    if ("conflict" in result) return err(409, "revision_stale", `Ревизия ${body.base_revision} устарела: текущая ревизия ${result.conflict}. Обновите результат и повторите просьбу.`, { current_revision: result.conflict });
    if ("busy" in result) return err(409, "repair_in_progress", "Предыдущая правка этой ревизии ещё применяется", { repair_job_id: result.busy });
    await delay(200);
    return HttpResponse.json({ edit_job_id: result.repair_job_id }, { status: 202 });
  }),

  http.post(base("/generations/:jobId/variants/:variantId/patches"), async ({ params, request }) => {
    settleRepairs();
    const g = store.generations.get(String(params.jobId));
    if (!g) return err(404, "job_not_found", "Задание не найдено");
    const variant = g.variants.find((v) => v.variant_id === String(params.variantId));
    if (!variant) return err(404, "variant_not_found", "Вариант не найден");
    const body = (await request.json()) as { base_revision: number; slides?: Array<{ slide_id: string; overrides: Override[] }>; order?: string[] };
    const slides = body.slides ?? [];
    if (slides.length === 0 && !body.order) return err(422, "patch_empty", "В запросе нет ни правок, ни нового порядка");
    const result = createPatch(g, variant.variant_id, body.base_revision, slides, body.order);
    if ("conflict" in result) return err(409, "revision_stale", `Ревизия ${body.base_revision} устарела: текущая ревизия ${result.conflict}. Обновите результат и повторите правки.`, { current_revision: result.conflict });
    if ("busy" in result) return err(409, "repair_in_progress", "Предыдущая правка этой ревизии ещё применяется", { repair_job_id: result.busy });
    if ("invalid" in result) return err(422, result.invalid.startsWith("patch_empty") ? "patch_empty" : "patch_invalid", `Правки не применимы к этой ревизии: ${result.invalid}`);
    await delay(200);
    return HttpResponse.json({ patch_job_id: result.repair_job_id }, { status: 202 });
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
      const title = slideTitleAt(variant, revision, idx, SLIDE_TITLES[idx] ?? `Слайд ${idx + 1}`, SLIDE_TITLES);
      const png = await slidePng(`${g.job_id}:${name}:${hasIssue}:${title}`, title, subtitle, variantId === "compact" ? "#0077FF" : variantId === "balanced" ? "#FF3885" : "#520977");
      return new HttpResponse(png, { headers: { "Content-Type": "image/png" } });
    }
    if (name.includes("/media/")) {
      const png = await assetPng(`media:${name.split("/media/")[1]}`, "#0077FF", true);
      return new HttpResponse(png, { headers: { "Content-Type": "image/png" } });
    }
    if (name.endsWith("plan.json")) return HttpResponse.json(g.plan);
    if (name.endsWith("audit.json")) return HttpResponse.json(audit);
    if (name.endsWith("composed.json")) return HttpResponse.json(buildDeck(g, variantId, revision, SLIDE_TITLES));
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
