"use client";

import { Alert, Button, Group, SegmentedControl, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconArrowLeft, IconX } from "@tabler/icons-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { SlideImage, type Overlay } from "@/components/common/SlideImage";
import { api, ApiError, TERMINAL_STATES } from "@/lib/api/client";
import type { AuditReport, GenerationResult } from "@/lib/api/types";
import { usePolling } from "@/lib/api/usePolling";
import { VARIANT_LABELS } from "@/lib/format";
import { saveLastJob } from "@/lib/state/draft";

import { AuditPanel } from "./AuditPanel";
import { HowBuiltPanel } from "./HowBuiltPanel";
import { MetricsPanel } from "./MetricsPanel";
import { ProgressPanel } from "./ProgressPanel";
import { RevisionsPanel } from "./RevisionsPanel";
import { VariantCard } from "./VariantCard";

export function WorkspaceView({ jobId }: { jobId: string }) {
  const router = useRouter();
  const [layout, setLayout] = useState<"side" | "single">("side");
  const [slideIndex, setSlideIndex] = useState(0);
  const [selectedVariantRaw, setSelectedVariant] = useState<string | null>(null);
  const [chosenVariant, setChosenVariant] = useState<string | null>(null);
  const [revision, setRevision] = useState<number | null>(null);
  const [selectedIssues, setSelectedIssues] = useState<Set<string>>(new Set());
  const [activeIssue, setActiveIssue] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [repairJob, setRepairJob] = useState<string | null>(null);
  const [showHowBuilt, setShowHowBuilt] = useState(false);

  const job = usePolling<GenerationResult>(() => api.generations.get(jobId), (r) => TERMINAL_STATES.has(r.status) && !repairJob, [jobId, repairJob]);
  const result = job.data;

  useEffect(() => {
    saveLastJob(jobId);
  }, [jobId]);

  // Вариант по умолчанию: первый с готовыми файлами, иначе первый в списке. Производное значение, не эффект.
  const selectedVariant = selectedVariantRaw ?? (result?.variants.find((v) => v.artifacts?.pptx) ?? result?.variants[0])?.variant_id ?? null;
  const variant = result?.variants.find((v) => v.variant_id === selectedVariant) ?? null;
  const currentRevision = variant?.revision ?? 1;
  const viewRevision = revision ?? currentRevision;

  // Сброс выбора находок и ревизии при смене варианта или появлении новой ревизии: состояние с ключом.
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
    variant && auditReady ? () => api.generations.audit(jobId, variant.variant_id, viewRevision) : null,
    () => true,
    [jobId, variant?.variant_id, viewRevision, auditReady, currentRevision],
  );
  const prevAudit = usePolling<AuditReport>(
    variant && auditReady && viewRevision > 1 ? () => api.generations.audit(jobId, variant.variant_id, viewRevision - 1) : null,
    () => true,
    [jobId, variant?.variant_id, viewRevision, auditReady],
  );

  // Опрос задания исправления: по завершении обновляем результат и отчёт.
  const repairStatus = usePolling(repairJob ? () => api.jobs.get(repairJob) : null, (s) => TERMINAL_STATES.has(s.status), [repairJob]);
  const repairDone = Boolean(repairJob && repairStatus.data && TERMINAL_STATES.has(repairStatus.data.status));
  const [handledRepair, setHandledRepair] = useState<string | null>(null);
  useEffect(() => {
    if (!repairDone || handledRepair === repairJob) return;
    const revisionNumber = repairStatus.data?.result?.revision ?? "";
    // Побочные эффекты в микрозадаче, чтобы не вызывать setState синхронно внутри эффекта.
    queueMicrotask(() => {
      setHandledRepair(repairJob);
      setRepairJob(null);
      job.refresh();
      notifications.show({ color: "green", title: "Исправления применены", message: `Создана ревизия ${revisionNumber}. Затронутые слайды перепроверены.` });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repairDone, repairJob, handledRepair]);

  const maxSlides = useMemo(() => Math.max(1, ...(result?.variants.map((v) => v.artifacts?.thumbnails?.length ?? v.slide_count ?? 0) ?? [1])), [result]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement | null)?.tagName === "INPUT" || (e.target as HTMLElement | null)?.tagName === "TEXTAREA") return;
      if (e.key === "ArrowRight") setSlideIndex((i) => Math.min(i + 1, maxSlides - 1));
      if (e.key === "ArrowLeft") setSlideIndex((i) => Math.max(i - 1, 0));
      if (e.key === "Escape") {
        setActiveIssue(null);
        setShowHowBuilt(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [maxSlides]);

  const cancel = async () => {
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
    setBusy(true);
    try {
      const res = await api.jobs.retry(jobId);
      router.push(`/workspace?job=${encodeURIComponent(res.job_id)}`);
      setBusy(false);
    } catch (e) {
      notifications.show({ color: "red", title: "Не удалось повторить", message: e instanceof ApiError ? e.message : "" });
      setBusy(false);
    }
  };

  const repair = async () => {
    if (!variant || !audit.data) return;
    setBusy(true);
    try {
      const res = await api.generations.repair(jobId, variant.variant_id, audit.data.revision, [...selectedIssues]);
      setRepairJob(res.repair_job_id);
      setSelectedIssues(new Set());
      notifications.show({ color: "blue", title: "Исправление запущено", message: "Затронутые слайды пересобираются и перепроверяются." });
    } catch (e) {
      if (e instanceof ApiError && e.code === "revision_stale") {
        notifications.show({ color: "orange", title: "Ревизия устарела", message: e.message, icon: <IconX size={16} />, autoClose: 8000 });
        setRevision(null);
        audit.refresh();
        job.refresh();
      } else {
        notifications.show({ color: "red", title: "Исправление не запущено", message: e instanceof ApiError ? e.message : "" });
      }
    } finally {
      setBusy(false);
    }
  };

  const focusIssue = useCallback((issue: AuditReport["issues"][number]) => {
    setSlideIndex(issue.slide_index);
    setActiveIssue(issue.issue_id);
    setLayout("single");
  }, []);

  if (job.error && !result) {
    const notFound = job.error instanceof ApiError && job.error.status === 404;
    return (
      <Stack py="xl" align="flex-start">
        <Title order={3}>{notFound ? "Задание не найдено" : "Не удалось загрузить задание"}</Title>
        <Text c="dimmed">{job.error.message}</Text>
        <Button component={Link} href="/" leftSection={<IconArrowLeft size={16} />} variant="light">К новой презентации</Button>
      </Stack>
    );
  }
  if (!result) return <Text c="dimmed" py="xl">Загружаем задание…</Text>;

  const overlays: Overlay[] =
    audit.data?.issues.filter((i) => i.slide_index === slideIndex && i.bbox).map((i) => ({ id: i.issue_id, bbox: i.bbox!, severity: i.severity, label: i.message })) ?? [];
  const thumb = variant?.artifacts?.thumbnails?.find((t) => t.slide_index === slideIndex);
  const thumbName = thumb && viewRevision !== currentRevision ? thumb.name.replace(`/r${currentRevision}/`, `/r${viewRevision}/`) : thumb?.name;

  return (
    <Stack gap="md" py="md">
      <Group justify="space-between">
        <Button component={Link} href="/" variant="subtle" size="compact-sm" leftSection={<IconArrowLeft size={14} />}>Новая презентация</Button>
        <Group gap="sm">
          <Text size="sm" c="dimmed">Слайд {slideIndex + 1} из {maxSlides} · стрелки ← → листают</Text>
          <SegmentedControl size="xs" value={layout} onChange={(v) => setLayout(v as "side" | "single")} data={[{ value: "side", label: "Рядом" }, { value: "single", label: "По одному" }]} data-testid="layout-switch" />
        </Group>
      </Group>

      <ProgressPanel result={result} onCancel={cancel} onRetry={retry} busy={busy} />

      {layout === "side" ? (
        <SimpleGrid cols={{ base: 1, md: result.variants.length }} spacing="md" data-testid="variants-grid">
          {result.variants.map((v) => (
            <VariantCard key={v.variant_id} jobId={jobId} variant={v} slideIndex={slideIndex} selected={v.variant_id === selectedVariant} chosen={v.variant_id === chosenVariant} onSelect={() => setSelectedVariant(v.variant_id)} onChoose={() => setChosenVariant(v.variant_id)} onSlideChange={setSlideIndex} />
          ))}
        </SimpleGrid>
      ) : (
        variant && (
          <Group align="flex-start" gap="md" wrap="nowrap">
            <Stack gap="xs" style={{ flex: 1, minWidth: 0 }}>
              <Group justify="space-between">
                <Group gap="xs">
                  <SegmentedControl size="xs" value={variant.variant_id} onChange={setSelectedVariant} data={result.variants.map((v) => ({ value: v.variant_id, label: VARIANT_LABELS[v.variant_id] ?? v.variant_id }))} />
                  <Text size="xs" c="dimmed">ревизия {viewRevision}</Text>
                </Group>
                <Button variant="subtle" size="compact-xs" onClick={() => setShowHowBuilt((s) => !s)} data-testid="toggle-how-built">{showHowBuilt ? "Скрыть «как собран»" : "Как собран слайд"}</Button>
              </Group>
              <SlideImage src={thumbName ? api.generations.artifactUrl(jobId, thumbName) : undefined} alt={`Слайд ${slideIndex + 1}`} overlays={overlays} activeOverlay={activeIssue} onOverlayClick={(id) => setActiveIssue(id)} />
              <div className="thumb-strip" data-testid="thumb-strip">
                {(variant.artifacts?.thumbnails ?? []).map((t) => (
                  <button key={t.name} type="button" data-active={t.slide_index === slideIndex} onClick={() => setSlideIndex(t.slide_index)} aria-label={`Слайд ${t.slide_index + 1}`}>
                    <SlideImage src={api.generations.artifactUrl(jobId, t.name)} alt={`Слайд ${t.slide_index + 1}`} />
                  </button>
                ))}
              </div>
              {showHowBuilt && <HowBuiltPanel jobId={jobId} result={result} variantId={variant.variant_id} slideIndex={slideIndex} />}
            </Stack>
            <Stack gap="md" style={{ width: 460, flex: "0 0 460px" }}>
              <AuditPanel report={audit.data} loading={audit.loading} stale={viewRevision !== currentRevision} selected={selectedIssues} activeIssue={activeIssue} onToggle={(id) => setSelectedIssues((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; })} onFocus={focusIssue} onRepair={repair} repairing={busy || Boolean(repairJob)} />
              <RevisionsPanel jobId={jobId} variant={variant} revision={viewRevision} onRevision={setRevision} issuesBefore={prevAudit.data?.summary.issues_total} issuesAfter={audit.data?.summary.issues_total} />
            </Stack>
          </Group>
        )
      )}

      {layout === "side" && variant && auditReady && (
        <Alert color="blue" variant="light">
          Аудит варианта «{VARIANT_LABELS[variant.variant_id]}»: {variant.audit?.issues_total ?? 0} находок. Переключитесь в режим «По одному», чтобы увидеть рамки на слайдах и выбрать исправления.
          <Button ml="md" size="compact-xs" variant="light" onClick={() => setLayout("single")} data-testid="open-audit">Открыть аудит</Button>
        </Alert>
      )}

      <MetricsPanel result={result} />
    </Stack>
  );
}
