"use client";

import { Badge, Button, Card, Group, Menu, Stack, Text, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDownload, IconStar, IconStarFilled } from "@tabler/icons-react";

import { SlideImage } from "@/components/common/SlideImage";
import { StatusBadge } from "@/components/common/StatusBadge";
import { api, ApiError } from "@/lib/api/client";
import { downloadArtifact } from "@/lib/download";
import type { GenerationResult } from "@/lib/api/types";
import { VARIANT_LABELS } from "@/lib/format";

type Variant = GenerationResult["variants"][number];

interface Props {
  jobId: string;
  variant: Variant;
  slideIndex: number;
  selected: boolean;
  chosen: boolean;
  onSelect: () => void;
  onChoose: () => void;
  onSlideChange: (i: number) => void;
}

export function VariantCard({ jobId, variant, slideIndex, selected, chosen, onSelect, onChoose, onSlideChange }: Props) {
  const download = (name: string, ext: string) => async () => {
    try {
      await downloadArtifact(api.generations.artifactUrl(jobId, name), `${variant.variant_id}-r${variant.revision}.${ext}`);
    } catch (e) {
      notifications.show({ color: "red", title: "Файл не скачан", message: e instanceof ApiError ? e.message : "Неизвестная ошибка" });
    }
  };
  const thumbs = variant.artifacts?.thumbnails ?? [];
  const thumb = thumbs.find((t) => t.slide_index === slideIndex) ?? thumbs[Math.min(slideIndex, thumbs.length - 1)];
  const filesReady = Boolean(variant.artifacts?.pptx);
  const auditRunning = variant.audit?.status === "running" || variant.audit?.status === "pending";

  return (
    <Card padding="sm" style={{ borderColor: selected ? "var(--ink)" : undefined, cursor: "pointer" }} onClick={onSelect} data-testid={`variant-card-${variant.variant_id}`}>
      <Stack gap="xs">
        <Group justify="space-between" wrap="nowrap">
          <Group gap="xs">
            <Text fw={600}>{VARIANT_LABELS[variant.variant_id] ?? variant.variant_id}</Text>
            <StatusBadge status={variant.status} />
          </Group>
          <Tooltip label={chosen ? "Выбранный вариант" : "Отметить как выбранный для демонстрации"} withArrow>
            <Button variant="subtle" size="compact-xs" color={chosen ? "yellow" : "gray"} onClick={(e) => { e.stopPropagation(); onChoose(); }} aria-label="Выбрать вариант" data-testid={`choose-${variant.variant_id}`}>
              {chosen ? <IconStarFilled size={16} /> : <IconStar size={16} />}
            </Button>
          </Tooltip>
        </Group>
        <SlideImage src={thumb ? api.generations.artifactUrl(jobId, thumb.name) : undefined} alt={`${variant.variant_id}, слайд ${slideIndex + 1}`} />
        <Group justify="space-between">
          <Group gap={4}>
            <Button variant="default" size="compact-xs" disabled={slideIndex <= 0} onClick={(e) => { e.stopPropagation(); onSlideChange(slideIndex - 1); }} aria-label="Предыдущий слайд">‹</Button>
            <Text size="xs" c="dimmed">{thumbs.length ? `${Math.min(slideIndex + 1, thumbs.length)} / ${thumbs.length}` : variant.slide_count ? `${variant.slide_count} слайдов` : "—"}</Text>
            <Button variant="default" size="compact-xs" disabled={slideIndex >= thumbs.length - 1} onClick={(e) => { e.stopPropagation(); onSlideChange(slideIndex + 1); }} aria-label="Следующий слайд">›</Button>
          </Group>
          <Group gap={4}>
            {variant.audit && variant.audit.status !== "pending" && (
              <Badge size="sm" variant="light" color={variant.audit.issues_total ? "yellow" : variant.audit.coverage_complete ? "green" : "gray"}>
                {auditRunning ? "аудит идёт" : `${variant.audit.issues_total} находок${variant.audit.coverage_complete ? "" : " · аудит неполный"}`}
              </Badge>
            )}
          </Group>
        </Group>
        <Text size="xs" c="dimmed" lineClamp={2} title={variant.rationale}>
          Ось «плотность»: {variant.rationale}
        </Text>
        {variant.error && <Text size="xs" c="red">{variant.error.message}</Text>}
        <Menu withinPortal position="bottom-start" disabled={!filesReady}>
          <Menu.Target>
            <Button variant="light" size="xs" leftSection={<IconDownload size={14} />} disabled={!filesReady} onClick={(e) => e.stopPropagation()} data-testid={`download-${variant.variant_id}`}>
              Скачать{auditRunning && filesReady ? " (аудит ещё идёт)" : ""}
            </Button>
          </Menu.Target>
          <Menu.Dropdown onClick={(e) => e.stopPropagation()}>
            {variant.artifacts?.pptx && <Menu.Item onClick={download(variant.artifacts.pptx, "pptx")} data-testid={`dl-pptx-${variant.variant_id}`}>PPTX, нативные объекты</Menu.Item>}
            {variant.artifacts?.pdf && <Menu.Item onClick={download(variant.artifacts.pdf, "pdf")} data-testid={`dl-pdf-${variant.variant_id}`}>PDF</Menu.Item>}
            {variant.artifacts?.html && <Menu.Item onClick={download(variant.artifacts.html, "html")} data-testid={`dl-html-${variant.variant_id}`}>HTML, автономный просмотр</Menu.Item>}
          </Menu.Dropdown>
        </Menu>
      </Stack>
    </Card>
  );
}
