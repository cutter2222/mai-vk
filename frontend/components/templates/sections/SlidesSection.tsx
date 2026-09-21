"use client";

import { Badge, Group, SimpleGrid, Stack, Text } from "@mantine/core";
import { useState } from "react";

import { SlideImage } from "@/components/common/SlideImage";
import { api } from "@/lib/api/client";
import type { TemplateProfile } from "@/lib/api/types";
import { SLIDE_CLASS_LABELS } from "@/lib/format";

import { countBy, FilterChips, SlideLightbox, percent } from "./common";

const CLASS_COLORS: Record<string, string> = { content_sample: "green", style_guide: "blue", asset_catalog: "yellow", empty: "gray", hidden: "gray", other: "gray" };

interface Props {
  templateId: string;
  profile: TemplateProfile;
  /** Имена всех отрендеренных слайдов файла из ответа сервера. */
  previews: string[];
}

type Row = { key: string; index: number; preview?: string; classification?: string; confidence?: number; layout?: string; group?: string };

/**
 * Все слайды файла с тем, как их классифицировал анализатор: образцы содержания идут в композиции,
 * инструкции — в правила, каталоги — в ресурсы. Без классификации показываются просто рендеры.
 */
export function SlidesSection({ templateId, profile, previews }: Props) {
  const [cls, setCls] = useState<string | null>(null);
  const [open, setOpen] = useState<Row | null>(null);
  const layouts = new Map(profile.layouts.map((l) => [l.layout_id, l.name]));

  const rows: Row[] = profile.sample_slides?.length
    ? profile.sample_slides.map((s) => ({ key: `s${s.slide_index}`, index: s.slide_index, preview: s.preview_path, classification: s.classification, confidence: s.confidence, layout: s.layout_id ? layouts.get(s.layout_id) ?? s.layout_id : undefined, group: s.group_id }))
    : previews.filter((name) => !/(^|\/)layout-/.test(name)).map((name, i) => ({ key: name, index: i + 1, preview: name }));
  const counts = countBy(rows.filter((r) => r.classification), (r) => r.classification as string);
  const shown = cls ? rows.filter((r) => r.classification === cls) : rows;
  const src = (r: Row) => (r.preview ? api.templates.assetUrl(templateId, r.preview) : undefined);

  return (
    <Stack gap="md">
      {Object.keys(counts).length > 0 ? (
        <FilterChips counts={counts} labels={SLIDE_CLASS_LABELS} active={cls} onChange={setCls} total={rows.length} testId="slide-filter" />
      ) : (
        <Text size="sm" c="dimmed">Классификации слайдов в профиле нет — показаны рендеры файла.</Text>
      )}
      {rows.length === 0 && <Text size="sm" c="dimmed">Миниатюры слайдов не сохранены: анализ прошёл без рендерера.</Text>}
      <SimpleGrid cols={{ base: 1, sm: 2, md: 3, xl: 4 }} spacing="lg">
        {shown.map((r) => (
          <button key={r.key} type="button" className="sample-card" onClick={() => setOpen(r)} data-testid={`sample-slide-${r.index}`}>
            <SlideImage src={src(r)} alt={`Слайд ${r.index}`} />
            <Group gap={6} mt={6} wrap="nowrap">
              <Text size="xs" fw={500} style={{ flex: "0 0 auto" }}>Слайд {r.index}</Text>
              {r.classification && <Badge size="xs" variant="light" color={CLASS_COLORS[r.classification] ?? "gray"}>{SLIDE_CLASS_LABELS[r.classification] ?? r.classification}</Badge>}
            </Group>
            <Text size="xs" c="dimmed" truncate>{[r.layout, r.group ? `группа ${r.group}` : null, r.confidence != null ? `уверенность ${percent(r.confidence)}` : null].filter(Boolean).join(" · ")}</Text>
          </button>
        ))}
      </SimpleGrid>
      <SlideLightbox
        src={open ? src(open) : undefined}
        title={open ? `Слайд ${open.index}` : ""}
        caption={open ? [open.classification ? SLIDE_CLASS_LABELS[open.classification] ?? open.classification : null, open.layout ? `макет «${open.layout}»` : null, open.group ? `группа ${open.group}` : null, open.confidence != null ? `уверенность ${percent(open.confidence)}` : null].filter(Boolean).join(" · ") : undefined}
        onClose={() => setOpen(null)}
      />
    </Stack>
  );
}
