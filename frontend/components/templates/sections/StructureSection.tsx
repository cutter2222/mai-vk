"use client";

import { Badge, Button, Group, Stack, Table, Text } from "@mantine/core";
import { useState } from "react";

import type { TemplateProfile } from "@/lib/api/types";
import { ASSET_KIND_LABELS, formatNumber } from "@/lib/format";

import { countBy, FilterChips, KeyValues, Section } from "./common";

const STAT_LABELS: Array<[keyof TemplateProfile["stats"], string]> = [
  ["slides", "Слайдов"],
  ["layouts", "Макетов"],
  ["masters", "Мастеров"],
  ["media", "Медиафайлов"],
  ["native_charts", "Нативных графиков"],
  ["native_tables", "Нативных таблиц"],
  ["smartart", "SmartArt"],
  ["embedded_fonts", "Встроенных шрифтов"],
  ["notes_with_text", "Заметок с текстом"],
];

const ASSETS_PREVIEW = 24;

/** Что в самом файле: мастера и макеты с заполнителями, ресурсы по видам, статистика пакета. */
export function StructureSection({ profile }: { profile: TemplateProfile }) {
  const [assetKind, setAssetKind] = useState<string | null>(null);
  const [allAssets, setAllAssets] = useState(false);
  const masters = new Map((profile.masters ?? []).map((m) => [m.master_id, m]));
  const assetCounts = countBy(profile.assets, (a) => a.kind);
  const assets = assetKind ? profile.assets.filter((a) => a.kind === assetKind) : profile.assets;
  const shownAssets = allAssets ? assets : assets.slice(0, ASSETS_PREVIEW);

  return (
    <Stack gap={28}>
      <Stack gap={28}>
        <Section title="Файл" testId="structure-stats">
          <KeyValues rows={[...STAT_LABELS.filter(([k]) => profile.stats[k] != null).map(([k, label]) => [label, formatNumber(profile.stats[k] as number)] as [string, React.ReactNode]), ["Размер слайда", `${formatNumber(profile.slide_size.width_emu)} × ${formatNumber(profile.slide_size.height_emu)} EMU · ${profile.slide_size.aspect_ratio.toFixed(2)}:1`]]} />
        </Section>
        <Section title="Мастера" aside={<Text size="xs" c="dimmed">{profile.masters?.length ?? 0}</Text>} testId="structure-masters">
          {profile.masters?.length ? (
            <KeyValues rows={profile.masters.map((m) => [m.master_id, `${m.name ?? "без имени"}${m.theme_name ? ` · тема «${m.theme_name}»` : ""}`])} />
          ) : (
            <Text size="sm" c="dimmed">Сведений о мастерах нет.</Text>
          )}
        </Section>
      </Stack>

      <Section title="Макеты" aside={<Text size="xs" c="dimmed">{profile.layouts.length}</Text>} testId="structure-layouts">
        <div style={{ overflowX: "auto" }}>
          <Table fz="xs" verticalSpacing={4} striped>
            <Table.Thead>
              <Table.Tr><Table.Th>Макет</Table.Th><Table.Th>Мастер</Table.Th><Table.Th>Заполнители</Table.Th><Table.Th>Образцов</Table.Th></Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {profile.layouts.map((l) => (
                <Table.Tr key={l.layout_id}>
                  <Table.Td><Text size="xs" fw={500}>{l.name}</Text><Text size="xs" c="dimmed">{l.layout_id}</Text></Table.Td>
                  <Table.Td c="dimmed">{l.master_id ? masters.get(l.master_id)?.name ?? l.master_id : "—"}</Table.Td>
                  <Table.Td c="dimmed">{l.placeholders.length ? l.placeholders.map((p) => `${p.type}${p.font?.size_pt ? ` ${p.font.size_pt}pt` : ""}`).join(", ") : "—"}</Table.Td>
                  <Table.Td c="dimmed">{l.sample_slide_count ?? 0}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </div>
      </Section>

      <Section title="Ресурсы" aside={<Text size="xs" c="dimmed">{profile.assets.length}</Text>} testId="structure-assets">
        {profile.assets.length === 0 ? (
          <Text size="sm" c="dimmed">Картинок, иконок и логотипов в файле не найдено.</Text>
        ) : (
          <Stack gap="sm">
            <FilterChips counts={assetCounts} labels={ASSET_KIND_LABELS} active={assetKind} onChange={(k) => { setAssetKind(k); setAllAssets(false); }} total={profile.assets.length} />
            <div style={{ overflowX: "auto" }}>
              <Table fz="xs" verticalSpacing={2} withRowBorders={false}>
                <Table.Tbody>
                  {shownAssets.map((a) => (
                    <Table.Tr key={a.asset_id}>
                      <Table.Td style={{ whiteSpace: "nowrap" }}>{a.asset_id}</Table.Td>
                      <Table.Td><Badge size="xs" variant="light" color="gray">{ASSET_KIND_LABELS[a.kind] ?? a.kind}</Badge></Table.Td>
                      <Table.Td c="dimmed">{a.media_path}</Table.Td>
                      <Table.Td c="dimmed" style={{ whiteSpace: "nowrap" }}>{a.width_px && a.height_px ? `${a.width_px} × ${a.height_px}` : "—"}</Table.Td>
                      <Table.Td c="dimmed" style={{ whiteSpace: "nowrap" }}>{a.source_slide_index != null ? `слайд ${a.source_slide_index}` : ""}</Table.Td>
                      <Table.Td c="dimmed">{[a.reusable ? "переиспользуемый" : null, ...(a.tags ?? [])].filter(Boolean).join(", ")}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </div>
            {assets.length > ASSETS_PREVIEW && (
              <Group>
                <Button variant="subtle" color="gray" size="compact-xs" onClick={() => setAllAssets((v) => !v)}>{allAssets ? "Свернуть" : `Показать все ${assets.length}`}</Button>
              </Group>
            )}
          </Stack>
        )}
      </Section>
    </Stack>
  );
}
