"use client";

import { Badge, Button, Group, Stack, Table, Text } from "@mantine/core";
import { useState } from "react";

import type { FixedElement, TemplateProfile } from "@/lib/api/types";
import { DYNAMIC_FIELD_LABELS, FIXED_KIND_LABELS, formatRatio, GUIDELINE_KIND_LABELS } from "@/lib/format";

import { countBy, Section, StatGrid } from "./common";

const FIXED_PREVIEW = 12;

/** Палитра, шрифты, шкала, поля и сетка, постоянные элементы и правила из текста шаблона. */
export function DesignSystemSection({ profile }: { profile: TemplateProfile }) {
  return (
    <Stack gap={28}>
      {/* Палитра, шрифты и шкала кеглей показаны выше, в дизайн-коде: там они даны
          образцами, а не таблицами с частотой употребления. Здесь — то, чего там нет:
          геометрия полей, постоянные элементы, правила из текста шаблона и заглушки. */}
      <Stack gap={28}>
        <Section title="Поля, направляющие и постоянные элементы" testId="design-sketch">
          {/* Эскиз занимает половину: во всю ширину он вырастал в экран высотой, а числа
              полей и направляющих ютились подписью под ним. Справа — те же величины словами. */}
          <div className="sketch-row">
            <FrameSketch profile={profile} />
            <FrameFacts profile={profile} />
          </div>
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
    <Group gap={6} wrap="nowrap"><span style={{ display: "inline-block", flex: "0 0 auto", ...style }} /><Text size="xs" c="dimmed">{text}</Text></Group>
  );
}

/** Подпись к эскизу: поля по сторонам, направляющие с координатами, сетка колонок и легенда. */
function FrameFacts({ profile }: { profile: TemplateProfile }) {
  const { spacing } = profile.design_tokens;
  const m = spacing?.margins;
  const guides = profile.guides ?? [];
  const fixedOnAll = profile.fixed_elements.filter((f) => f.appears_on === "all").length;
  const vertical = guides.filter((g) => g.orientation === "vertical");
  const horizontal = guides.filter((g) => g.orientation === "horizontal");
  const list = (items: typeof guides, axis: "x" | "y") => items.map((g) => `${axis} = ${formatRatio(g.pos)}`).join(", ");
  return (
    <div className="sketch-facts" data-testid="frame-facts">
      <div className="sketch-fact">
        <Text size="xs" fw={600} c="dimmed" tt="uppercase">Поля</Text>
        {m ? (
          <StatGrid columns={4} items={[["слева", formatRatio(m.left)], ["сверху", formatRatio(m.top)], ["справа", formatRatio(m.right)], ["снизу", formatRatio(m.bottom)]]} />
        ) : <Text size="sm" c="dimmed" mt={4}>Не определены: образцы не дают устойчивого отступа.</Text>}
        <Text size="xs" c="dimmed" mt={6}>Доля ширины и высоты слайда; внутри полей стоит содержание образцов.</Text>
      </div>
      <div className="sketch-fact">
        <Text size="xs" fw={600} c="dimmed" tt="uppercase">Направляющие</Text>
        {guides.length ? (
          <Stack gap={2} mt={4}>
            {vertical.length > 0 && <Text size="sm">Вертикальные · {vertical.length}: <Text span c="dimmed">{list(vertical, "x")}</Text></Text>}
            {horizontal.length > 0 && <Text size="sm">Горизонтальные · {horizontal.length}: <Text span c="dimmed">{list(horizontal, "y")}</Text></Text>}
            <Text size="xs" c="dimmed" mt={4}>{guides.some((g) => g.source === "view_props") ? "Из файла шаблона" : "Выведены по образцам"}; по ним выравниваются новые слайды.</Text>
          </Stack>
        ) : <Text size="sm" c="dimmed" mt={4}>Не найдены.</Text>}
      </div>
      <div className="sketch-fact">
        <Text size="xs" fw={600} c="dimmed" tt="uppercase">Сетка и постоянные элементы</Text>
        <Stack gap={2} mt={4}>
          <Text size="sm">{spacing?.column_grid ? <>Колонок {spacing.column_grid.columns ?? "—"}, межколонник <Text span c="dimmed">{spacing.column_grid.gutter != null ? formatRatio(spacing.column_grid.gutter) : "—"}</Text></> : "Сетка колонок не выведена"}</Text>
          <Text size="sm">На всех слайдах · {fixedOnAll} {plural(fixedOnAll, ["элемент", "элемента", "элементов"])}</Text>
        </Stack>
      </div>
      <Group gap="md" className="sketch-legend">
        <Legend swatch="dashed" text="поля" />
        <Legend swatch="line" text="направляющие" />
        <Legend swatch="box" text="постоянные элементы" />
      </Group>
    </div>
  );
}

function plural(n: number, forms: [string, string, string]) {
  const mod10 = n % 10, mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return forms[0];
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return forms[1];
  return forms[2];
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

const appears = (v: string) => (v === "all" ? "все слайды" : v.startsWith("layout:") ? `макет ${v.slice(7)}` : v);

function FixedElements({ items, dynamic }: { items: FixedElement[]; dynamic: NonNullable<TemplateProfile["dynamic_fields"]> }) {
  const [all, setAll] = useState(false);
  const counts = countBy(items, (f) => f.kind);
  const shown = all ? items : items.slice(0, FIXED_PREVIEW);
  return (
    <Section title="Постоянные элементы" aside={<Text size="xs" c="dimmed">{items.length}</Text>} testId="design-fixed">
      <Text size="sm" c="dimmed" mb="sm">Логотипы, колонтитулы и декор, которые повторяются на слайдах: при сборке остаются на месте, содержание их не перекрывает.</Text>
      <Group gap={6} mb="md">
        {Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, n]) => <Badge key={k} variant="light" color="gray">{FIXED_KIND_LABELS[k] ?? k} · {n}</Badge>)}
        {items.length === 0 && <Text size="sm" c="dimmed">Постоянных элементов (логотип, колонтитул, декор мастера) не найдено.</Text>}
      </Group>
      {items.length > 0 && (
        <Table.ScrollContainer minWidth={560}>
          <Table fz="sm" verticalSpacing={6} className="detail-table" data-testid="fixed-table">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Элемент</Table.Th>
                <Table.Th>Где</Table.Th>
                <Table.Th>Положение</Table.Th>
                <Table.Th>Размер</Table.Th>
                <Table.Th>Правило</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {shown.map((f) => (
                <Table.Tr key={f.element_id}>
                  <Table.Td>{FIXED_KIND_LABELS[f.kind] ?? f.kind}</Table.Td>
                  <Table.Td c="dimmed">{appears(f.appears_on)}</Table.Td>
                  <Table.Td c="dimmed" style={{ whiteSpace: "nowrap" }}>{formatRatio(f.bbox.x)} · {formatRatio(f.bbox.y)}</Table.Td>
                  <Table.Td c="dimmed" style={{ whiteSpace: "nowrap" }}>{formatRatio(f.bbox.width)} × {formatRatio(f.bbox.height)}</Table.Td>
                  <Table.Td c="dimmed">{f.must_not_move ? "не двигать" : "—"}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
      {items.length > FIXED_PREVIEW && (
        <Button variant="subtle" color="gray" size="compact-xs" mt={4} onClick={() => setAll((v) => !v)}>{all ? "Свернуть" : `Показать все ${items.length}`}</Button>
      )}
      {dynamic.length > 0 && (
        <div style={{ marginTop: 24 }} data-testid="dynamic-fields">
          <Text fw={600} size="sm">Обновляются при перестановке слайдов</Text>
          <Text size="xs" c="dimmed" mt={2} mb="sm">Номера, даты и счётчики: сборка подставляет в них актуальные значения.</Text>
          <Table.ScrollContainer minWidth={420}>
            <Table fz="sm" verticalSpacing={6} className="detail-table">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Поле</Table.Th>
                  <Table.Th>Где</Table.Th>
                  <Table.Th>Элемент</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {dynamic.map((d, i) => (
                  <Table.Tr key={`${d.kind}-${i}`}>
                    <Table.Td>{DYNAMIC_FIELD_LABELS[d.kind] ?? d.kind}</Table.Td>
                    <Table.Td c="dimmed">{appears(d.appears_on)}</Table.Td>
                    <Table.Td c="dimmed">{d.element_ref ?? "—"}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </div>
      )}
    </Section>
  );
}
