"use client";

import { Badge, Button, Group, Stack, Table, Text } from "@mantine/core";
import { useState } from "react";

import type { FixedElement, TemplateProfile } from "@/lib/api/types";
import { DYNAMIC_FIELD_LABELS, FIXED_KIND_LABELS, formatRatio, GUIDELINE_KIND_LABELS } from "@/lib/format";

import { countBy, KeyValues, Section } from "./common";

const FIXED_PREVIEW = 12;

/** Палитра, шрифты, шкала, поля и сетка, постоянные элементы и правила из текста шаблона. */
export function DesignSystemSection({ profile }: { profile: TemplateProfile }) {
  const { spacing } = profile.design_tokens;
  return (
    <Stack gap={28}>
      {/* Палитра, шрифты и шкала кеглей показаны выше, в дизайн-коде: там они даны
          образцами, а не таблицами с частотой употребления. Здесь — то, чего там нет:
          геометрия полей, постоянные элементы, правила из текста шаблона и заглушки. */}
      <Stack gap={28}>
        <Section title="Поля, направляющие и постоянные элементы" testId="design-sketch">
          <FrameSketch profile={profile} />
          <Group gap="md" mt="sm">
            <Legend swatch="dashed" text={spacing?.margins ? `поля ${formatRatio(spacing.margins.left)} · ${formatRatio(spacing.margins.top)} · ${formatRatio(spacing.margins.right)} · ${formatRatio(spacing.margins.bottom)}` : "поля не определены"} />
            <Legend swatch="line" text={`направляющих ${profile.guides?.length ?? 0}`} />
            <Legend swatch="box" text="постоянные элементы на всех слайдах" />
          </Group>
          {spacing?.column_grid && <Text size="xs" c="dimmed" mt={6}>Колонок: {spacing.column_grid.columns ?? "—"}, межколонник {spacing.column_grid.gutter != null ? formatRatio(spacing.column_grid.gutter) : "—"}.</Text>}
        </Section>
        <FixedElements items={profile.fixed_elements} dynamic={profile.dynamic_fields ?? []} />
      </Stack>

      <Stack gap={28}>
        <Section title="Правила из текста шаблона" aside={<Text size="xs" c="dimmed">{profile.guidelines?.length ?? 0}</Text>} testId="design-guidelines">
          {profile.guidelines?.length ? (
            <Stack gap={6}>
              {profile.guidelines.map((g, i) => (
                <Group key={i} gap="sm" wrap="nowrap" align="flex-start">
                  <Badge size="xs" variant="light" color="gray" style={{ flex: "0 0 auto", marginTop: 2 }}>{GUIDELINE_KIND_LABELS[g.kind] ?? g.kind}</Badge>
                  <Text size="sm">«{g.text}»{g.source_slide_index != null && <Text span size="xs" c="dimmed"> · слайд {g.source_slide_index}</Text>}</Text>
                </Group>
              ))}
            </Stack>
          ) : (
            <Text size="sm" c="dimmed">Инструкций по оформлению в файле не найдено.</Text>
          )}
        </Section>
        <Section title="Строки-заглушки образцов" aside={<Text size="xs" c="dimmed">{profile.placeholder_markers?.length ?? 0}</Text>} testId="design-markers">
          {profile.placeholder_markers?.length ? (
            <Group gap={6}>
              {profile.placeholder_markers.map((m) => <Badge key={m} variant="default" color="gray" style={{ textTransform: "none" }}>{m}</Badge>)}
            </Group>
          ) : (
            <Text size="sm" c="dimmed">Заглушек вроде «Заголовок» или «Lorem ipsum» в образцах нет.</Text>
          )}
          <Text size="xs" c="dimmed" mt="sm">Такие строки убираются из результата, а их появление в готовом слайде ловит аудит.</Text>
        </Section>
      </Stack>
    </Stack>
  );
}

function Legend({ swatch, text }: { swatch: "dashed" | "line" | "box"; text: string }) {
  const style: React.CSSProperties = swatch === "dashed" ? { width: 14, height: 10, border: "1px dashed var(--mantine-color-gray-5)" } : swatch === "line" ? { width: 14, height: 2, background: "var(--mantine-color-blue-4)" } : { width: 14, height: 10, background: "rgba(44, 47, 55, 0.35)", border: "1px solid rgba(44, 47, 55, 0.6)" };
  return (
    <Group gap={6} wrap="nowrap"><span style={{ display: "inline-block", ...style }} /><Text size="xs" c="dimmed">{text}</Text></Group>
  );
}

/** Эскиз слайда 16:9: поля пунктиром, направляющие линиями, постоянные элементы «на всех слайдах» — прямоугольниками. */
function FrameSketch({ profile }: { profile: TemplateProfile }) {
  const m = profile.design_tokens.spacing?.margins;
  const fixed = profile.fixed_elements.filter((f) => f.appears_on === "all" && f.bbox.width > 0.002 && f.bbox.height > 0.002);
  const pct = (v: number) => `${Math.max(0, Math.min(100, v * 100))}%`;
  return (
    <div className="frame-sketch" data-testid="frame-sketch">
      {m && <div className="sketch-margins" style={{ left: pct(m.left), top: pct(m.top), right: pct(m.right), bottom: pct(m.bottom) }} />}
      {profile.guides?.map((g, i) => (
        <div key={i} className="sketch-guide" data-orientation={g.orientation} title={`${g.orientation === "vertical" ? "x" : "y"} = ${formatRatio(g.pos)} · ${g.source === "view_props" ? "из файла" : "выведена"}`} style={g.orientation === "vertical" ? { left: pct(g.pos) } : { top: pct(g.pos) }} />
      ))}
      {fixed.map((f) => (
        <div key={f.element_id} className="sketch-fixed" data-kind={f.kind} title={`${FIXED_KIND_LABELS[f.kind] ?? f.kind} · ${f.element_id}`} style={{ left: pct(f.bbox.x), top: pct(f.bbox.y), width: pct(f.bbox.width), height: pct(f.bbox.height) }} />
      ))}
    </div>
  );
}

function FixedElements({ items, dynamic }: { items: FixedElement[]; dynamic: NonNullable<TemplateProfile["dynamic_fields"]> }) {
  const [all, setAll] = useState(false);
  const counts = countBy(items, (f) => f.kind);
  const shown = all ? items : items.slice(0, FIXED_PREVIEW);
  const appears = (v: string) => (v === "all" ? "все слайды" : v.startsWith("layout:") ? `макет ${v.slice(7)}` : v);
  return (
    <Section title="Постоянные элементы" aside={<Text size="xs" c="dimmed">{items.length}</Text>} testId="design-fixed">
      <Group gap={6} mb="sm">
        {Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, n]) => <Badge key={k} variant="light" color="gray">{FIXED_KIND_LABELS[k] ?? k} · {n}</Badge>)}
        {items.length === 0 && <Text size="sm" c="dimmed">Постоянных элементов (логотип, колонтитул, декор мастера) не найдено.</Text>}
      </Group>
      {items.length > 0 && (
        <Table fz="xs" verticalSpacing={2} withRowBorders={false}>
          <Table.Tbody>
            {shown.map((f) => (
              <Table.Tr key={f.element_id}>
                <Table.Td>{FIXED_KIND_LABELS[f.kind] ?? f.kind}</Table.Td>
                <Table.Td c="dimmed">{appears(f.appears_on)}</Table.Td>
                <Table.Td c="dimmed" style={{ whiteSpace: "nowrap" }}>{formatRatio(f.bbox.x)}, {formatRatio(f.bbox.y)} · {formatRatio(f.bbox.width)} × {formatRatio(f.bbox.height)}</Table.Td>
                <Table.Td c="dimmed">{f.must_not_move ? "не двигать" : ""}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {items.length > FIXED_PREVIEW && (
        <Button variant="subtle" color="gray" size="compact-xs" mt={4} onClick={() => setAll((v) => !v)}>{all ? "Свернуть" : `Показать все ${items.length}`}</Button>
      )}
      {dynamic.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Text size="xs" c="dimmed" mb={4}>Обновляются при перестановке слайдов</Text>
          <KeyValues rows={dynamic.map((d, i) => [`${DYNAMIC_FIELD_LABELS[d.kind] ?? d.kind}${dynamic.filter((x) => x.kind === d.kind).length > 1 ? ` ${i + 1}` : ""}`, `${appears(d.appears_on)}${d.element_ref ? ` · элемент ${d.element_ref}` : ""}`])} />
        </div>
      )}
    </Section>
  );
}
