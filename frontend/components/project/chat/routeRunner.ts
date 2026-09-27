"use client";

/**
 * Исполнение решения роутера чата (этап 37): шаги идут по очереди в существующие исполнители —
 * правка копии на сервере (объект, текст, логотип, картинка), перестройка слайда по плану,
 * починка находок проверки, отмена. Каждая правка копии — карточка `edit_result` с «Отменить»;
 * перестройка и починка — карточка `edit_card`, как раньше.
 */

import type { OfficeEditHandle, OfficeEditOutcome } from "@/components/office/OfficeEditor";
import { api, ApiError, TERMINAL_STATES, type RouteStep } from "@/lib/api/client";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";
import { plural } from "@/lib/format";
import { appendMessage, getProject, patchMessage, type ChatMessage } from "@/lib/state/projects";
import type { SlideTarget } from "@/lib/hooks/useGenerationSession";

export interface RunContext {
  projectId: string;
  session: GenerationSession;
  office: OfficeEditHandle | null;
  /** Число слайдов в живом редакторе: не совпадает с планом — слайды добавлены руками. */
  liveCount: number | null;
  say: (text: string) => void;
  /** Перестройка слайда без повторного сообщения пользователя; id задания или null. */
  rebuild: (instruction: string, target: SlideTarget) => Promise<string | null>;
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function applied(ctx: RunContext, jobId: string, variantId: string, revision: number, editJobId: string): Promise<void> {
  const office = ctx.office;
  if (!office) return;
  if (!office.waitForApplied) throw new Error("Редактор не подтвердил перенос правки. Следующие шаги не выполняю.");
  await office.waitForApplied(jobId, variantId, revision, editJobId);
}

/** Ждёт конца задания правки: следующая перестройка идёт на её результате. */
async function settle(jobId: string, limitMs = 300000): Promise<boolean> {
  const until = Date.now() + limitMs;
  while (Date.now() < until) {
    await wait(2000);
    try {
      const status = await api.jobs.get(jobId);
      if (TERMINAL_STATES.has(status.status)) return status.status === "succeeded";
    } catch {
      /* сеть моргнула — спросим ещё раз */
    }
  }
  return false;
}

/** Карточка итога правки копии: текст и «Отменить»; без изменений — фраза. */
function report(ctx: RunContext, outcome: OfficeEditOutcome, slides: number[]): void {
  const message = outcome.message.trim() || "Готово.";
  if (!outcome.changed) {
    ctx.say(`Документ не изменён. ${message}`);
    return;
  }
  const lead = slides.length === 1 ? `Слайд ${slides[0]}: ` : slides.length > 1 ? `Слайды ${slides.join(", ")}: ` : "";
  appendMessage(ctx.projectId, {
    role: "assistant",
    kind: "edit_result",
    text: lead ? lead + message.charAt(0).toLowerCase() + message.slice(1) : message,
    document_id: outcome.documentId,
    revision: outcome.revision,
    base_revision: outcome.base,
    slides,
  });
}

function errorText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

async function officeStep(ctx: RunContext, step: RouteStep, request: Parameters<OfficeEditHandle["run"]>[0]): Promise<boolean> {
  if (!ctx.office) {
    ctx.say("Эта правка делается в открытой презентации: дождитесь, пока она откроется справа.");
    return false;
  }
  try {
    report(ctx, await ctx.office.run(request), step.slides);
    return true;
  } catch (e) {
    ctx.say(errorText(e, "Правка не применена."));
    return false;
  }
}

async function rebuildSlides(ctx: RunContext, slides: number[], instruction: string): Promise<boolean> {
  const jobId = ctx.session.jobId;
  const variant = ctx.session.variant;
  if (!jobId || !variant?.artifacts?.pptx || variant.status === "failed") {
    ctx.say("Перестроить слайд пока нельзя: презентация ещё не собрана.");
    return false;
  }
  const count = variant.slide_count ?? 0;
  const missing = slides.find((n) => count && n > count);
  if (missing) {
    ctx.say(`В презентации ${count} ${plural(count, "слайд", "слайда", "слайдов")}, слайда ${missing} нет.`);
    return false;
  }
  if (ctx.liveCount && count && ctx.liveCount !== count) {
    ctx.say("Слайды в редакторе добавлены или удалены вручную, поэтому пересобрать слайд из чата не получится. Выделите объект на слайде и напишите, что с ним сделать.");
    return false;
  }
  let revision = variant.revision;
  for (const slide of slides) {
    const editJob = await ctx.rebuild(instruction, { jobId, variantId: variant.variant_id, revision, slideIndex: slide - 1 });
    if (!editJob) return false;
    // Следующий слайд — на ревизии, которую дала эта правка.
    if (!(await settle(editJob))) {
      ctx.say(`Правка слайда ${slide} не завершилась — остальные слайды не трогаю.`);
      return false;
    }
    try {
      const result = await api.generations.get(jobId);
      revision = result.variants.find((v) => v.variant_id === variant.variant_id)?.revision ?? revision;
      ctx.session = { ...ctx.session, result, variant: result.variants.find((v) => v.variant_id === variant.variant_id) ?? variant };
      ctx.session.job.refresh();
      const entry = result.edits?.find((e) => e.edit_job_id === editJob);
      if (ctx.office && !entry) throw new Error("Сервер не подтвердил результат правки. Следующие шаги не выполняю.");
      if (entry?.result === "applied" && entry.new_revision) await applied(ctx, jobId, variant.variant_id, entry.new_revision, editJob);
    } catch (e) {
      ctx.say(errorText(e, "Не удалось подтвердить перенос правки в презентацию."));
      return false;
    }
  }
  return true;
}

/** Номера слайдов плана ревизии по их идентификаторам: починка отдаёт слайды идентификаторами. */
export async function slideNumbers(jobId: string, variantId: string, revision: number, slideIds: string[]): Promise<number[]> {
  if (!slideIds.length) return [];
  const plan = await api.generations.artifactJson<{ slides: Array<{ slide_id: string; order?: number }> }>(jobId, `${variantId}/r${revision}/plan.json`);
  const ordered = [...plan.slides].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  return ordered.map((s, i) => (slideIds.includes(s.slide_id) ? i + 1 : 0)).filter(Boolean);
}

async function repair(ctx: RunContext, slides: number[]): Promise<boolean> {
  const jobId = ctx.session.jobId;
  const variant = ctx.session.variant;
  if (!jobId || !variant?.artifacts?.pptx) {
    ctx.say("Исправлять пока нечего: презентация ещё не собрана.");
    return false;
  }
  try {
    const audit = await api.generations.audit(jobId, variant.variant_id, variant.revision);
    const issues = (audit.issues ?? []).filter((i) => !slides.length || (typeof i.slide_index === "number" && slides.includes(i.slide_index + 1)));
    if (!issues.length) {
      ctx.say(slides.length ? `На ${slides.length > 1 ? "этих слайдах" : `слайде ${slides[0]}`} замечаний проверки нет.` : "Замечаний проверки нет.");
      return true;
    }
    const res = await api.generations.repair(jobId, variant.variant_id, variant.revision, issues.map((i) => i.issue_id));
    appendMessage(ctx.projectId, { role: "assistant", kind: "edit_card", job_id: jobId, variant_id: variant.variant_id, edit_job_id: res.repair_job_id, slide_index: Math.max(0, (slides[0] ?? 1) - 1) });
    if (!(await settle(res.repair_job_id))) {
      ctx.say("Исправление не завершилось — следующие шаги не выполняю.");
      return false;
    }
    const result = await api.generations.get(jobId);
    ctx.session = { ...ctx.session, result, variant: result.variants.find((v) => v.variant_id === variant.variant_id) ?? variant };
    ctx.session.job.refresh();
    const entry = result.repairs?.find((r) => r.repair_job_id === res.repair_job_id);
    if (ctx.office && !entry) throw new Error("Сервер не подтвердил результат исправления. Следующие шаги не выполняю.");
    if (entry?.result === "applied" && entry.new_revision) await applied(ctx, jobId, variant.variant_id, entry.new_revision, res.repair_job_id);
    return true;
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) ctx.say("Отчёт проверки ещё не готов — повторите через минуту.");
    else ctx.say(`Не удалось запустить исправление: ${errorText(e, "ошибка сервера")}`);
    return false;
  }
}

/** Последняя правка из чата, которую можно отменить: итог правки копии или перестройка. */
function lastUndoable(projectId: string, session: GenerationSession): ChatMessage | null {
  const events = getProject(projectId)?.events ?? [];
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const e = events[i];
    if (e.role !== "assistant") continue;
    if (e.kind === "edit_result" && !e.undone) return e;
    if (e.kind === "edit_card" && !e.undone && e.job_id === session.jobId && e.variant_id === session.variant?.variant_id) {
      const entry = session.result?.edits?.find((x) => x.edit_job_id === e.edit_job_id);
      if (entry?.result === "applied") return e;
    }
  }
  return null;
}

/** Отмена правки по событию ленты (кнопка карточки) или последней («отмени»). */
export async function undo(ctx: RunContext, eventId?: string): Promise<boolean> {
  const events = getProject(ctx.projectId)?.events ?? [];
  const target = eventId ? events.find((e) => e.event_id === eventId) ?? null : lastUndoable(ctx.projectId, ctx.session);
  if (!target || target.role !== "assistant") {
    ctx.say("Отменять нечего: из чата ещё ничего не меняли.");
    return false;
  }
  if (target.kind === "edit_result") {
    if (target.undone) return true;
    if (!ctx.office) {
      ctx.say("Отмена работает в открытой презентации: дождитесь, пока она откроется справа.");
      return false;
    }
    try {
      await ctx.office.undo(target.revision, target.base_revision, target.document_id);
      await patchMessage(ctx.projectId, target.event_id, { undone: true });
      const where = target.slides?.length ? ` на ${target.slides.length > 1 ? `слайдах ${target.slides.join(", ")}` : `слайде ${target.slides[0]}`}` : "";
      ctx.say(`Отменил правку${where}: вернул как было.`);
      return true;
    } catch (e) {
      ctx.say(errorText(e, "Отменить не получилось."));
      return false;
    }
  }
  if (target.kind === "edit_card") {
    if (target.undone) return true;
    if (target.job_id !== ctx.session.jobId || target.variant_id !== ctx.session.variant?.variant_id) {
      ctx.say("Откройте вариант презентации, в котором была сделана эта правка.");
      return false;
    }
    const entry = ctx.session.result?.edits?.find((x) => x.edit_job_id === target.edit_job_id);
    if (!entry || entry.result !== "applied" || !entry.new_revision) {
      ctx.say("Эту правку отменить нельзя: она не применилась.");
      return false;
    }
    try {
      const res = await api.generations.revert(target.job_id, target.variant_id, entry.new_revision, entry.base_revision, entry.slide_index);
      if (!(await settle(res.edit_job_id))) {
        ctx.say("Отмена не завершилась — следующие шаги не выполняю.");
        return false;
      }
      await patchMessage(ctx.projectId, target.event_id, { undone: true });
      const result = await api.generations.get(target.job_id);
      ctx.session = { ...ctx.session, result, variant: result.variants.find((v) => v.variant_id === target.variant_id) ?? ctx.session.variant };
      ctx.session.job.refresh();
      await applied(ctx, target.job_id, target.variant_id, ctx.session.variant!.revision, res.edit_job_id);
      return true;
    } catch (e) {
      ctx.say(errorText(e, "Отменить не получилось."));
      return false;
    }
  }
  ctx.say("Отменять нечего: из чата ещё ничего не меняли.");
  return false;
}

/** Шаги по порядку; ошибка шага останавливает следующие, сделанное остаётся. */
export async function runSteps(ctx: RunContext, steps: RouteStep[]): Promise<void> {
  for (const step of steps) {
    const instruction = step.instruction?.trim() ?? "";
    let ok = true;
    switch (step.action) {
      case "object_edit":
        if (!ctx.office || !step.target) {
          ok = await rebuildSlides(ctx, step.slides, instruction);
          break;
        }
        ok = await officeStep(ctx, step, {
          instruction,
          live: { slide: step.target.slide, name: step.target.name, ...(step.target.box ? { box: step.target.box } : {}), ...(step.target.in_group ? { in_group: true } : {}) },
        });
        break;
      case "slide_rebuild":
        ok = await rebuildSlides(ctx, step.slides, instruction);
        break;
      case "deck_text":
        if (!ctx.office && step.slides.length) {
          ok = await rebuildSlides(ctx, step.slides, instruction);
          break;
        }
        ok = await officeStep(ctx, step, { instruction, ...(step.slides.length ? { slides: step.slides } : {}) });
        break;
      case "logo":
        if (step.logo === "replace" && !step.file_id) {
          ctx.say("Прикрепите картинку нового логотипа (PNG или JPG) — заменю его на всех слайдах сразу.");
          ok = false;
          break;
        }
        ok = await officeStep(ctx, step, { instruction, logo: { action: step.logo ?? "remove", ...(step.file_id ? { file_id: step.file_id } : {}) } });
        break;
      case "image":
        if (!step.file_id || !step.slides[0]) {
          ok = false;
          break;
        }
        ok = await officeStep(ctx, step, {
          instruction,
          image: { file_id: step.file_id, slide: step.slides[0] },
          ...(step.target ? { live: { slide: step.target.slide, name: step.target.name, ...(step.target.box ? { box: step.target.box } : {}), ...(step.target.in_group ? { in_group: true } : {}) } } : {}),
        });
        break;
      case "table":
        if (!step.file_id || !step.slides[0]) {
          ok = false;
          break;
        }
        ok = await officeStep(ctx, step, { instruction, table: { file_id: step.file_id, slide: step.slides[0] } });
        break;
      case "chart":
        if (!step.slides[0] || !step.source || (step.source !== "editable" && !step.file_id)) {
          ok = false;
          break;
        }
        ok = await officeStep(ctx, step, {
          instruction,
          chart: { source: step.source, slide: step.slides[0], ...(step.file_id ? { file_id: step.file_id } : {}) },
        });
        break;
      case "repair":
        ok = await repair(ctx, step.slides);
        break;
      case "undo":
        ok = await undo(ctx);
        break;
    }
    if (!ok) return;
  }
}
