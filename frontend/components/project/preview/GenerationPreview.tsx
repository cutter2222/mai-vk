"use client";

import { ActionIcon, Badge, Button, Group, Loader, SegmentedControl, SimpleGrid, Stack, Text, Tooltip } from "@mantine/core";
import { IconListCheck, IconStar, IconStarFilled, IconX } from "@tabler/icons-react";

import { AuditPanel } from "@/components/workspace/AuditPanel";
import { HowBuiltPanel } from "@/components/workspace/HowBuiltPanel";
import { RevisionsPanel } from "@/components/workspace/RevisionsPanel";
import { VariantCard } from "@/components/workspace/VariantCard";
import { api } from "@/lib/api/client";
import { formatMs, STAGE_LABELS, VARIANT_LABELS } from "@/lib/format";
import { useElapsed } from "@/lib/hooks/useElapsed";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";

import { SlideViewer } from "./SlideViewer";

interface Props {
  session: GenerationSession;
  chosenVariant: string | null;
  onChoose: (variantId: string | null) => void;
}

const VARIANT_DOT: Record<string, string> = { pending: "gray", running: "blue", ready: "green", needs_review: "yellow", failed: "red" };

/** Слайды сгенерированной презентации: переключение вариантов, просмотр по одному или рядом, рамки аудита. */
export function GenerationPreview({ session, chosenVariant, onChoose }: Props) {
  const { jobId, result, variant } = session;
  const elapsed = useElapsed(result?.created_at, session.terminal ? (result?.finished_at ?? result?.created_at) : null);
  if (!jobId || !result) return null;

  const thumbs = variant?.artifacts?.thumbnails ?? [];
  const issueSlides = new Set(session.audit.data?.issues.map((i) => i.slide_index) ?? []);
  const slides = thumbs.map((t) => ({
    key: t.name,
    src: api.generations.artifactUrl(jobId, session.viewRevision !== session.currentRevision ? t.name.replace(`/r${session.currentRevision}/`, `/r${session.viewRevision}/`) : t.name),
    label: `Слайд ${t.slide_index + 1}`,
    flagged: issueSlides.has(t.slide_index),
  }));
  const running = !session.terminal;
  const isChosen = Boolean(variant && chosenVariant === variant.variant_id);

  return (
    <>
      <div className="preview-toolbar">
        <Group gap="sm" wrap="nowrap">
          <SegmentedControl
            size="xs"
            value={variant?.variant_id ?? ""}
            onChange={session.setSelectedVariant}
            data={result.variants.map((v) => ({
              value: v.variant_id,
              label: (
                <Group gap={6} wrap="nowrap">
                  <span style={{ width: 7, height: 7, borderRadius: "50%", background: `var(--mantine-color-${VARIANT_DOT[v.status] ?? "gray"}-6)` }} />
                  {VARIANT_LABELS[v.variant_id] ?? v.variant_id}
                </Group>
              ),
            }))}
            data-testid="variant-switch"
          />
          {variant && (
            <Tooltip label={isChosen ? "Выбранный вариант для демонстрации" : "Отметить как выбранный для демонстрации"}>
              <ActionIcon variant="subtle" color={isChosen ? "yellow" : "gray"} onClick={() => onChoose(isChosen ? null : variant.variant_id)} aria-label="Выбрать вариант" data-testid={`choose-${variant.variant_id}`}>
                {isChosen ? <IconStarFilled size={18} /> : <IconStar size={18} />}
              </ActionIcon>
            </Tooltip>
          )}
        </Group>
        <Group gap="sm" wrap="nowrap">
          <SegmentedControl size="xs" value={session.layout} onChange={(v) => session.setLayout(v as "single" | "side")} data={[{ value: "single", label: "Один вариант" }, { value: "side", label: "Сравнить" }]} data-testid="layout-switch" />
          {variant?.audit && variant.audit.status !== "pending" && (
            <Button
              size="xs"
              variant={session.auditOpen ? "filled" : "default"}
              leftSection={<IconListCheck size={14} />}
              onClick={() => {
                session.setLayout("single");
                session.setAuditOpen(!session.auditOpen);
              }}
              data-testid="toggle-audit"
            >
              Аудит{variant.audit.issues_total ? ` · ${variant.audit.issues_total}` : ""}
            </Button>
          )}
        </Group>
      </div>

      {running && (
        <div style={{ padding: "8px 20px", borderBottom: "1px solid var(--mantine-color-gray-2)", background: "var(--mantine-color-body)" }} data-testid="preview-progress">
          <Group justify="space-between" wrap="nowrap">
            <Group gap={6} wrap="nowrap"><Loader size={12} /><Text size="xs">{result.progress?.message ?? STAGE_LABELS[result.stage]}</Text></Group>
            <Text size="xs" fw={600} style={{ whiteSpace: "nowrap" }}>{formatMs(elapsed)}</Text>
          </Group>
        </div>
      )}

      {session.layout === "side" ? (
        <div className="preview-scroll">
          <SimpleGrid cols={{ base: 1, md: result.variants.length }} spacing="md" data-testid="variants-grid">
            {result.variants.map((v) => (
              <VariantCard
                key={v.variant_id}
                jobId={jobId}
                variant={v}
                slideIndex={session.slideIndex}
                selected={v.variant_id === session.selectedVariant}
                chosen={v.variant_id === chosenVariant}
                onSelect={() => session.setSelectedVariant(v.variant_id)}
                onChoose={() => onChoose(chosenVariant === v.variant_id ? null : v.variant_id)}
                onSlideChange={session.selectSlide}
              />
            ))}
          </SimpleGrid>
        </div>
      ) : variant && slides.length === 0 ? (
        <div className="preview-empty">
          <Stack align="center" gap={6} maw={420}>
            <Text fw={600}>{variant.status === "failed" ? "Вариант не собран" : "Слайды появятся, как только вариант будет собран"}</Text>
            <Text size="sm" c="dimmed" ta="center">{variant.error?.message ?? variant.rationale}</Text>
          </Stack>
        </div>
      ) : variant ? (
        <SlideViewer
          slides={slides}
          index={session.slideIndex}
          onIndex={session.selectSlide}
          overlays={session.overlays}
          activeOverlay={session.activeIssue}
          onOverlayClick={session.setActiveIssue}
          caption={
            <>
              <Text size="sm" fw={500}>{VARIANT_LABELS[variant.variant_id] ?? variant.variant_id}</Text>
              <Text size="xs" c="dimmed">ревизия {session.viewRevision}</Text>
              {session.viewRevision !== session.currentRevision && <Badge size="xs" color="orange" variant="light">устаревшая</Badge>}
              {variant.status === "running" && <Badge size="xs" color="blue" variant="light" data-testid="preview-provisional">предварительный показ · сборка идёт</Badge>}
              {variant.audit && variant.audit.status !== "pending" && (
                <Badge size="xs" variant="light" color={variant.audit.status === "running" ? "blue" : variant.audit.issues_total ? "yellow" : variant.audit.coverage_complete ? "green" : "gray"}>
                  {variant.audit.status === "running" ? "аудит идёт" : `${variant.audit.issues_total} находок${variant.audit.coverage_complete ? "" : " · аудит неполный"}`}
                </Badge>
              )}
            </>
          }
          actions={
            <Tooltip label="Клавиши ↑ ↓ и ← → листают слайды, Esc снимает выделение">
              <Button variant="subtle" size="compact-xs" onClick={() => session.setShowHowBuilt(!session.showHowBuilt)} data-testid="toggle-how-built">
                {session.showHowBuilt ? "Скрыть «как собран»" : "Как собран слайд"}
              </Button>
            </Tooltip>
          }
          aside={
            session.auditOpen ? (
              <div className="audit-drawer" data-testid="audit-drawer">
                <Group justify="space-between" mb="sm">
                  <Text fw={600}>Аудит</Text>
                  <ActionIcon variant="subtle" color="gray" size="sm" onClick={() => session.setAuditOpen(false)} aria-label="Закрыть аудит"><IconX size={14} /></ActionIcon>
                </Group>
                <AuditPanel
                  report={session.audit.data}
                  loading={session.audit.loading}
                  stale={session.viewRevision !== session.currentRevision}
                  selected={session.selectedIssues}
                  activeIssue={session.activeIssue}
                  onToggle={session.toggleIssue}
                  onFocus={session.focusIssue}
                  onRepair={() => void session.repair()}
                  repairing={session.busy || Boolean(session.repairJob) || Boolean(session.editJob)}
                />
                {(variant.revisions?.length ?? 0) > 1 && (
                  <div className="panel-section">
                    <RevisionsPanel
                      jobId={jobId}
                      variant={variant}
                      revision={session.viewRevision}
                      onRevision={session.setRevision}
                      issuesBefore={session.prevAudit.data?.summary.issues_total}
                      issuesAfter={session.audit.data?.summary.issues_total}
                    />
                  </div>
                )}
              </div>
            ) : null
          }
        >
          <Stack gap="md" mt="xs">
            <Text size="xs" c="dimmed" lineClamp={2} title={variant.rationale}>Ось «плотность»: {variant.rationale}</Text>
            {session.showHowBuilt && <HowBuiltPanel jobId={jobId} result={result} variantId={variant.variant_id} slideIndex={session.slideIndex} />}
          </Stack>
        </SlideViewer>
      ) : null}
    </>
  );
}
