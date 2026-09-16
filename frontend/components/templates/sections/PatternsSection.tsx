"use client";

import { Badge, Group, Modal, SimpleGrid, Stack, Switch, Table, Text } from "@mantine/core";
import { useState } from "react";

import { SlideImage, type Overlay } from "@/components/common/SlideImage";
import { api } from "@/lib/api/client";
import type { Pattern, Slot, TemplateProfile } from "@/lib/api/types";
import { formatRatio, PATTERN_ROLE_LABELS, ROLE_SOURCE_LABELS, SLOT_KIND_LABELS } from "@/lib/format";

import { countBy, FilterChips, KeyValues, percent } from "./common";

const POSITION_LABELS: Record<string, string> = { first: "первый", early: "в начале", middle: "в середине", late: "ближе к концу", last: "последний", any: "любое" };

interface Props {
  templateId: string;
  profile: TemplateProfile;
}

/** Композиции из образцов: сетка с фильтром по роли, по клику — слоты поверх образца и их параметры. */
export function PatternsSection({ templateId, profile }: Props) {
  const [role, setRole] = useState<string | null>(null);
  const [selected, setSelected] = useState<Pattern | null>(null);
  const counts = countBy(profile.patterns, (p) => p.role);
  const shown = role ? profile.patterns.filter((p) => p.role === role) : profile.patterns;
  const src = (p: Pattern) => (p.preview_path ? api.templates.assetUrl(templateId, p.preview_path) : undefined);

  return (
    <Stack gap="md">
      <FilterChips counts={counts} labels={PATTERN_ROLE_LABELS} active={role} onChange={setRole} total={profile.patterns.length} testId="pattern-filter" />
      {shown.length === 0 && <Text size="sm" c="dimmed">Композиций нет: в файле не нашлось образцов содержания.</Text>}
      <SimpleGrid cols={{ base: 2, sm: 3, md: 4, lg: 5 }} spacing="md">
        {shown.map((p) => (
          <button key={p.pattern_id} type="button" className="sample-card" onClick={() => setSelected(p)} data-testid={`pattern-card-${p.pattern_id}`}>
            <SlideImage src={src(p)} alt={p.name ?? p.role} />
            <Group gap={4} mt={6} wrap="nowrap">
              <Badge size="xs" variant="light">{PATTERN_ROLE_LABELS[p.role] ?? p.role}</Badge>
              <Text size="xs" truncate title={p.name}>{p.name ?? p.pattern_id}</Text>
            </Group>
            <Text size="xs" c="dimmed">{p.slots.length} слотов · уверенность {percent(p.confidence)}{p.source.slide_index != null ? ` · слайд ${p.source.slide_index}` : ""}</Text>
          </button>
        ))}
      </SimpleGrid>
      <PatternModal pattern={selected} src={selected ? src(selected) : undefined} onClose={() => setSelected(null)} />
    </Stack>
  );
}

function slotOverlays(pattern: Pattern): Overlay[] {
  return pattern.slots.map((s) => ({ id: s.slot_id, bbox: s.bbox, severity: "slot", label: `${s.slot_id} · ${SLOT_KIND_LABELS[s.kind] ?? s.kind}` }));
}

function fontLabel(slot: Slot): string {
  const f = slot.computed_style?.font ?? slot.font;
  if (!f) return "—";
  return [f.family, f.size_pt ? `${f.size_pt} pt` : null, f.bold ? "жирный" : null, f.italic ? "курсив" : null, f.color].filter(Boolean).join(" · ");
}

function capacityLabel(slot: Slot): string {
  const c = slot.capacity;
  if (!c) return "—";
  const parts = [c.max_chars != null ? `${c.max_chars} зн.` : null, c.max_lines != null ? `${c.max_lines} стр.` : null, c.max_items != null ? `${c.max_items} эл.` : null, c.max_words_per_item != null ? `${c.max_words_per_item} сл./эл.` : null];
  const method = c.measured_with?.method === "font_metrics" ? "по метрикам шрифта" : c.measured_with?.method === "heuristic" ? "эвристика" : null;
  return [parts.filter(Boolean).join(", "), method].filter(Boolean).join(" · ") || "—";
}

const bboxLabel = (b: Slot["bbox"]) => `${formatRatio(b.x)}, ${formatRatio(b.y)} · ${formatRatio(b.width)} × ${formatRatio(b.height)}`;

/** Композиция крупно: рамки слотов на образце, откуда роль, ограничения и таблица слотов. */
export function PatternModal({ pattern, src, onClose }: { pattern: Pattern | null; src?: string; onClose: () => void }) {
  const [showSlots, setShowSlots] = useState(true);
  if (!pattern) return null;
  const c = pattern.constraints;
  const hints = pattern.sequence_hints;
  return (
    <Modal opened onClose={onClose} size="min(1180px, 94vw)" centered title={<Group gap="xs"><Badge variant="light">{PATTERN_ROLE_LABELS[pattern.role] ?? pattern.role}</Badge><Text fw={600}>{pattern.name ?? pattern.pattern_id}</Text></Group>}>
      <Stack gap="md" data-testid="pattern-modal">
        <div>
          <SlideImage src={src} alt={pattern.name ?? pattern.role} overlays={showSlots ? slotOverlays(pattern) : []} />
          <Group justify="space-between" mt={6}>
            <Text size="xs" c="dimmed">{pattern.slots.length} слотов · рамки — области, куда планировщик кладёт содержание</Text>
            <Switch size="xs" label="Показать слоты" checked={showSlots} onChange={(e) => setShowSlots(e.currentTarget.checked)} />
          </Group>
        </div>
        <KeyValues
          rows={[
            ["Идентификатор", pattern.pattern_id],
            ["Источник", `${pattern.source.kind === "sample_slide" ? "образец" : "макет"}${pattern.source.slide_index != null ? `, слайд ${pattern.source.slide_index}` : ""} · ${pattern.source.layout_id}${pattern.source.pptx_slide_part ? ` · ${pattern.source.pptx_slide_part}` : ""}`],
            ["Роль определена", `${ROLE_SOURCE_LABELS[pattern.role_source ?? ""] ?? pattern.role_source ?? "—"} · уверенность ${percent(pattern.confidence)}`],
            ["Ограничения", c ? [c.min_items != null || c.max_items != null ? `элементов ${c.min_items ?? "…"}–${c.max_items ?? "…"}` : null, c.supports?.length ? `поддерживает: ${c.supports.join(", ")}` : "без вставок"].filter(Boolean).join(" · ") : "—"],
            ["Место в колоде", hints ? [hints.typical_position ? POSITION_LABELS[hints.typical_position] ?? hints.typical_position : null, hints.max_consecutive != null ? `подряд не более ${hints.max_consecutive}` : null].filter(Boolean).join(" · ") || "—" : "—"],
            ["Объекты образца", `постоянных ${pattern.static_object_ids?.length ?? 0} · удаляемых ${pattern.removable_object_ids?.length ?? 0}`],
            ...(pattern.tags?.length ? [["Теги", pattern.tags.join(", ")] as [string, React.ReactNode]] : []),
            ...(pattern.notes ? [["Заметка", pattern.notes] as [string, React.ReactNode]] : []),
          ]}
        />
        <div style={{ overflowX: "auto" }}>
          <Table fz="xs" verticalSpacing={4} striped highlightOnHover data-testid="slots-table">
            <Table.Thead>
              <Table.Tr>
                <Table.Th style={{ whiteSpace: "nowrap" }}>Слот</Table.Th>
                <Table.Th>Тип</Table.Th>
                <Table.Th>Шрифт</Table.Th>
                <Table.Th>Ёмкость</Table.Th>
                <Table.Th>Выравнивание</Table.Th>
                <Table.Th>Область</Table.Th>
                <Table.Th>Текст образца</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {pattern.slots.map((s) => (
                <Table.Tr key={s.slot_id}>
                  <Table.Td style={{ whiteSpace: "nowrap" }}>
                    {s.slot_id}
                    {s.required && <Text span c="red" title="обязательный"> *</Text>}
                    {s.repeat_group && <Text span c="dimmed"> · группа {s.repeat_group}</Text>}
                  </Table.Td>
                  <Table.Td>{SLOT_KIND_LABELS[s.kind] ?? s.kind}</Table.Td>
                  <Table.Td>{fontLabel(s)}</Table.Td>
                  <Table.Td>{capacityLabel(s)}</Table.Td>
                  <Table.Td>{[s.align, s.valign].filter(Boolean).join(" / ") || "—"}</Table.Td>
                  <Table.Td style={{ whiteSpace: "nowrap" }}>{bboxLabel(s.bbox)}</Table.Td>
                  <Table.Td style={{ maxWidth: 220 }}><Text size="xs" truncate title={s.sample_text}>{s.sample_text ?? "—"}</Text></Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </div>
      </Stack>
    </Modal>
  );
}
