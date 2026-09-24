"use client";

import { notifications } from "@mantine/notifications";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { Overlay } from "@/components/common/SlideImage";
import { api, ApiError, TERMINAL_STATES } from "@/lib/api/client";
import type { AuditReport, GenerationResult, SlidePatch } from "@/lib/api/types";
import { usePolling } from "@/lib/api/usePolling";

type Variant = GenerationResult["variants"][number];
type Issue = AuditReport["issues"][number];

export type PreviewLayout = "single" | "side";

/** Адрес правки: выбранный слайд выбранного варианта в его текущей ревизии. */
export interface SlideTarget {
  jobId: string;
  variantId: string;
  revision: number;
  slideIndex: number;
}

/**
 * Состояние задания генерации для редактора проекта: опрос результата, выбранный вариант и слайд,
 * отчёт аудита нужной ревизии, выбор находок, исправления, отмена и повтор.
 * Используется одновременно панелью предпросмотра и панелью результата, поэтому живёт выше обеих.
 */
export function useGenerationSession(jobId: string | null, onNewJob: (jobId: string) => void, { loadAudit = true }: { loadAudit?: boolean } = {}) {
  const [layout, setLayout] = useState<PreviewLayout>("single");
  const [slideIndex, setSlideIndex] = useState(0);
  const [selectedVariantRaw, setSelectedVariant] = useState<string | null>(null);
  const [revision, setRevision] = useState<number | null>(null);
  const [selectedIssues, setSelectedIssues] = useState<Set<string>>(new Set());
  const [activeIssue, setActiveIssue] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [repairJob, setRepairJob] = useState<string | null>(null);
  const [showHowBuilt, setShowHowBuilt] = useState(false);
  const [editJob, setEditJob] = useState<string | null>(null);
  // Задание правки: инструкция из чата (edit) или ручные правки редактора (patch).
  const [editJobKind, setEditJobKind] = useState<"edit" | "patch">("edit");
  // Непустой черновик визуального редактора: чат не отправляет сообщения, пока правки не применены.
  const [editorDirty, setEditorDirty] = useState(false);
  // Крестик на чипе снимает адресацию для этого слайда; следующий выбор слайда возвращает чип.
  const [dismissedTarget, setDismissedTarget] = useState<number | null>(null);

  const job = usePolling<GenerationResult>(jobId ? () => api.generations.get(jobId) : null, (r) => TERMINAL_STATES.has(r.status) && !repairJob, [jobId, repairJob]);
  const result = job.data?.job_id === jobId ? job.data : null;

  // Сброс локального состояния при смене задания: состояние с ключом, без эффекта.
  const [prevJobId, setPrevJobId] = useState(jobId);
  if (prevJobId !== jobId) {
    setPrevJobId(jobId);
    setSlideIndex(0);
    setSelectedVariant(null);
    setRevision(null);
    setSelectedIssues(new Set());
    setActiveIssue(null);
    setRepairJob(null);
    setEditJob(null);
    setDismissedTarget(null);
    setLayout("single");
  }

  // Вариант по умолчанию: первый с готовыми файлами, иначе первый в списке. Первый готовый
  // закрепляется: варианты собираются по очереди, и доделанный следом «Компактный» (он раньше
  // в списке) не должен сам подменять уже открытую презентацию.
  const firstReady = result?.variants.find((v) => v.artifacts?.pptx)?.variant_id ?? null;
  if (selectedVariantRaw === null && firstReady) setSelectedVariant(firstReady);
  const selectedVariant = selectedVariantRaw ?? firstReady ?? result?.variants[0]?.variant_id ?? null;
  const variant: Variant | null = result?.variants.find((v) => v.variant_id === selectedVariant) ?? null;
  const currentRevision = variant?.revision ?? 1;
  const viewRevision = revision ?? currentRevision;

  // Сброс выбора находок и ревизии при смене варианта или появлении новой ревизии.
  const resetKey = `${selectedVariant}:${currentRevision}`;
  const [prevResetKey, setPrevResetKey] = useState(resetKey);
  if (prevResetKey !== resetKey) {
    setPrevResetKey(resetKey);
    setRevision(null);
    setSelectedIssues(new Set());
    setActiveIssue(null);
  }

  const auditReady = Boolean(variant && variant.audit && !["pending", "running"].includes(variant.audit.status ?? ""));
  const audit = usePolling<AuditReport>(
    loadAudit && jobId && variant && auditReady ? () => api.generations.audit(jobId, variant.variant_id, viewRevision) : null,
    () => true,
    [loadAudit, jobId, variant?.variant_id, viewRevision, auditReady, currentRevision],
  );
  const prevAudit = usePolling<AuditReport>(
    loadAudit && jobId && variant && auditReady && viewRevision > 1 ? () => api.generations.audit(jobId, variant.variant_id, viewRevision - 1) : null,
    () => true,
    [loadAudit, jobId, variant?.variant_id, viewRevision, auditReady],
  );

  // Опрос задания исправления: по завершении обновляем результат и отчёт.
  // Состояние опроса переживает смену задания: завершённым считается только ответ по текущему заданию.
  const repairStatus = usePolling(repairJob ? () => api.jobs.get(repairJob) : null, (s) => TERMINAL_STATES.has(s.status), [repairJob]);
  const repairDone = Boolean(repairJob && repairStatus.data?.job_id === repairJob && TERMINAL_STATES.has(repairStatus.data.status));
  const [handledRepair, setHandledRepair] = useState<string | null>(null);
  useEffect(() => {
    if (!repairDone || handledRepair === repairJob) return;
    const revisionNumber = repairStatus.data?.result?.revision ?? "";
    queueMicrotask(() => {
      setHandledRepair(repairJob);
      setRepairJob(null);
      job.refresh();
      notifications.show({ color: "green", title: "Исправления применены", message: `Создана ревизия ${revisionNumber}. Затронутые слайды перепроверены.` });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repairDone, repairJob, handledRepair]);

  // Опрос задания правки слайда: по завершении обновляем результат, ревизия сбрасывается на текущую.
  const editStatus = usePolling(editJob ? () => api.jobs.get(editJob) : null, (s) => TERMINAL_STATES.has(s.status), [editJob]);
  const editDone = Boolean(editJob && editStatus.data?.job_id === editJob && TERMINAL_STATES.has(editStatus.data.status));
  const [handledEdit, setHandledEdit] = useState<string | null>(null);
  useEffect(() => {
    if (!editDone || handledEdit === editJob) return;
    const status = editStatus.data;
    queueMicrotask(() => {
      setHandledEdit(editJob);
      setEditJob(null);
      job.refresh();
      const manual = editJobKind === "patch";
      if (status?.status === "succeeded" && status.result?.unchanged) {
        notifications.show({ color: "gray", title: "Слайд оставлен как есть", message: status.result.change_note ?? "" });
      } else if (status?.status === "succeeded") {
        notifications.show({
          color: "green",
          title: manual ? "Правки применены" : "Слайд изменён",
          message: manual
            ? `Создана ревизия ${status.result?.revision ?? ""}: ${status.result?.change_note ?? "правки редактора"}`
            : `Создана ревизия ${status.result?.revision ?? ""}. ${status.result?.change_note ?? ""}`.trim(),
        });
      } else if (status?.status === "failed") {
        notifications.show({ color: "red", title: manual ? "Правки не применены" : "Правка не применена", message: status.error?.message ?? "" });
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editDone, editJob, handledEdit]);

  // Слайдов у показываемого варианта, а не у самого длинного: варианты разной длины, и с
  // общим пределом стрелка с последнего слайда компактного уезжала на несуществующий номер,
  // а лента, не найдя его, показывала первый слайд — со стороны это «провалился в другой слайд».
  const variantSlides = useMemo(
    () => variant?.artifacts?.thumbnails?.length ?? variant?.slide_count ?? 0,
    [variant],
  );
  const maxSlides = useMemo(
    () =>
      variantSlides ||
      Math.max(1, ...(result?.variants.map((v) => v.artifacts?.thumbnails?.length ?? v.slide_count ?? 0) ?? [1])),
    [variantSlides, result],
  );

  // Смена варианта на более короткий: выбранный слайд подтягивается к последнему существующему.
  // Прямо при рендере, а не в эффекте: иначе один кадр показывал бы слайд, которого у варианта нет.
  if (variantSlides > 0 && slideIndex > variantSlides - 1) setSlideIndex(variantSlides - 1);

  useEffect(() => {
    if (!jobId) return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) return;
      // В открытом диалоге (крупный просмотр правки) стрелки принадлежат ему.
      if (target?.closest('[role="dialog"]')) return;
      // Слайды листаются и по вертикали, как в редакторах презентаций, и стрелками влево-вправо.
      if (e.key === "ArrowRight" || e.key === "ArrowDown") {
        e.preventDefault();
        setSlideIndex((i) => Math.min(i + 1, maxSlides - 1));
      }
      if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
        e.preventDefault();
        setSlideIndex((i) => Math.max(i - 1, 0));
      }
      if (e.key === "Escape") {
        setActiveIssue(null);
        setShowHowBuilt(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [jobId, maxSlides]);

  const cancel = async () => {
    if (!jobId) return;
    setBusy(true);
    try {
      await api.jobs.cancel(jobId);
      job.refresh();
    } catch (e) {
      notifications.show({ color: "red", title: "Не удалось отменить", message: e instanceof ApiError ? e.message : "" });
    } finally {
      setBusy(false);
    }
  };

  const retry = async () => {
    if (!jobId) return;
    setBusy(true);
    try {
      const res = await api.jobs.retry(jobId);
      onNewJob(res.job_id);
    } catch (e) {
      notifications.show({ color: "red", title: "Не удалось повторить", message: e instanceof ApiError ? e.message : "" });
    } finally {
      setBusy(false);
    }
  };

  const repair = async (issueIds?: string[]): Promise<string | null> => {
    if (!jobId || !variant || !audit.data || editorDirty || busy || editJob || repairJob) return null;
    const ids = issueIds ?? [...selectedIssues];
    if (ids.length === 0) return null;
    setBusy(true);
    try {
      const res = await api.generations.repair(jobId, variant.variant_id, audit.data.revision, ids);
      setRepairJob(res.repair_job_id);
      setSelectedIssues(new Set());
      return res.repair_job_id;
    } catch (e) {
      if (e instanceof ApiError && e.code === "revision_stale") {
        notifications.show({ color: "orange", title: "Ревизия устарела", message: e.message, autoClose: 8000 });
        setRevision(null);
        audit.refresh();
        job.refresh();
      } else {
        notifications.show({ color: "red", title: "Исправление не запущено", message: e instanceof ApiError ? e.message : "" });
      }
    } finally {
      setBusy(false);
    }
    return null;
  };

  /** Ручные правки редактора: одна ревизия на все перечисленные слайды и новый порядок; ошибки отдаются вызывающему. */
  const requestPatch = async (target: Pick<SlideTarget, "jobId" | "variantId" | "revision">, slides: SlidePatch["slides"], order?: string[], templateLogo?: "keep" | "drop"): Promise<string> => {
    setBusy(true);
    try {
      const res = await api.generations.patch(target.jobId, target.variantId, target.revision, slides, order, templateLogo);
      setEditJobKind("patch");
      setEditJob(res.patch_job_id);
      setHandledEdit(null);
      return res.patch_job_id;
    } catch (e) {
      if (e instanceof ApiError && e.code === "revision_stale") {
        setRevision(null);
        job.refresh();
      }
      throw e;
    } finally {
      setBusy(false);
    }
  };

  /** Правка выбранного слайда по инструкции; ошибки (устаревшая ревизия, идущая правка) отдаются вызывающему. */
  const requestEdit = async (target: SlideTarget, instruction: string): Promise<string> => {
    if (editorDirty) throw new Error("Сначала примените или сбросьте ручной черновик.");
    setBusy(true);
    try {
      const res = await api.generations.edit(target.jobId, target.variantId, target.revision, target.slideIndex, instruction);
      setEditJobKind("edit");
      setEditJob(res.edit_job_id);
      setHandledEdit(null);
      return res.edit_job_id;
    } catch (e) {
      if (e instanceof ApiError && e.code === "revision_stale") {
        setRevision(null);
        job.refresh();
      }
      throw e;
    } finally {
      setBusy(false);
    }
  };

  const toggleIssue = useCallback((id: string) => {
    setSelectedIssues((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }, []);

  /** Находка ведёт к своему месту: тот же слайд и подсвеченная рамка рядом с лентой. */
  const focusIssue = useCallback((issue: Issue) => {
    setSlideIndex(issue.slide_index);
    setActiveIssue(issue.issue_id);
    setLayout("single");
  }, []);

  const overlays: Overlay[] =
    audit.data?.issues.filter((i) => i.slide_index === slideIndex && i.bbox).map((i) => ({ id: i.issue_id, bbox: i.bbox!, severity: i.severity, label: i.message })) ?? [];
  const thumb = variant?.artifacts?.thumbnails?.find((t) => t.slide_index === slideIndex);
  const thumbName = thumb && viewRevision !== currentRevision ? thumb.name.replace(`/r${currentRevision}/`, `/r${viewRevision}/`) : thumb?.name;
  const terminal = Boolean(result && TERMINAL_STATES.has(result.status));

  // Адрес правки: выбранный слайд собранного варианта, пока задание завершено и чип не снят.
  // Готовая презентация показана раньше, чем разобрана: её слайд можно адресовать сразу —
  // сервер поставит правку за разбором и скажет, когда применит.
  const deckShown = variant?.variant_id === "original" && variant.status === "running" && Boolean(variant.artifacts?.pptx);
  const editable = (terminal || deckShown) && viewRevision === currentRevision && variant !== null && (variant.status === "ready" || variant.status === "needs_review" || deckShown) && Boolean(thumb);
  const slideTarget: SlideTarget | null =
    jobId && variant && editable && dismissedTarget !== slideIndex ? { jobId, variantId: variant.variant_id, revision: currentRevision, slideIndex } : null;
  const dismissTarget = useCallback(() => setDismissedTarget(slideIndex), [slideIndex]);
  const selectSlide = useCallback((i: number) => {
    setSlideIndex(i);
    setDismissedTarget(null);
  }, []);

  return {
    jobId,
    job,
    result,
    terminal,
    layout,
    setLayout,
    slideIndex,
    setSlideIndex,
    maxSlides,
    selectedVariant,
    setSelectedVariant,
    variant,
    currentRevision,
    viewRevision,
    setRevision,
    auditReady,
    audit,
    prevAudit,
    selectedIssues,
    toggleIssue,
    activeIssue,
    setActiveIssue,
    focusIssue,
    busy,
    repairJob,
    cancel,
    retry,
    repair,
    showHowBuilt,
    setShowHowBuilt,
    overlays,
    thumbName,
    slideTarget,
    dismissTarget,
    selectSlide,
    requestEdit,
    requestPatch,
    editJob,
    editJobKind,
    editStatus: editStatus.data,
    editorDirty,
    setEditorDirty,
  };
}

export type GenerationSession = ReturnType<typeof useGenerationSession>;
