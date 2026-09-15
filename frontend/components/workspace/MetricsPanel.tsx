"use client";

import { Group, SimpleGrid, Stack, Table, Text } from "@mantine/core";

import type { GenerationResult } from "@/lib/api/types";
import { formatMs, formatNumber, STAGE_LABELS, VARIANT_LABELS } from "@/lib/format";

export function MetricsPanel({ result }: { result: GenerationResult }) {
  const m = result.metrics;
  const tl = m.timeline;
  return (
    <div data-testid="metrics-panel">
      <SimpleGrid cols={2} spacing="sm" mb="md">
        <Kpi label="Всего времени" value={formatMs(m.totals.duration_ms)} />
        <Kpi label="Первый файл" value={formatMs(tl?.first_file_ready_ms)} />
        <Kpi label="Все варианты с аудитом" value={formatMs(tl?.all_variants_audited_ms)} />
        <Kpi label="Вызовов модели" value={formatNumber(m.totals.llm_calls)} />
        <Kpi label="Токены" value={`${formatNumber(m.totals.prompt_tokens)} / ${formatNumber(m.totals.completion_tokens)}`} hint="запрос / ответ" />
      </SimpleGrid>
      <Group gap="sm" mb="sm">
        <Text size="xs" c="dimmed">Ожидание очереди {formatMs(m.queue_wait_ms)}</Text>
        <Text size="xs" c="dimmed">Ожидание квоты провайдера {formatMs(m.quota_wait_ms)}</Text>
        <Text size="xs" c="dimmed">Повторов {m.retries ?? 0}</Text>
        {m.cache && <Text size="xs" c="dimmed">Кэш модели: {m.cache.llm_hits ?? 0} попаданий, профиль {m.cache.profile_hit ? "из кэша" : "свежий"}</Text>}
      </Group>
      <Table verticalSpacing={4} withRowBorders={false} fz="xs">
        <Table.Thead>
          <Table.Tr><Table.Th>Этап</Table.Th><Table.Th>Вариант</Table.Th><Table.Th>Время</Table.Th><Table.Th>Ожидание квоты</Table.Th></Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {(m.stages ?? []).map((s, i) => (
            <Table.Tr key={`c${i}`}><Table.Td>{STAGE_LABELS[s.stage]}</Table.Td><Table.Td>общий</Table.Td><Table.Td>{s.status === "done" ? formatMs(s.duration_ms) : s.status}</Table.Td><Table.Td>{formatMs(s.quota_wait_ms)}</Table.Td></Table.Tr>
          ))}
          {result.variants.flatMap((v) => (v.stages ?? []).map((s, i) => (
            <Table.Tr key={`${v.variant_id}${i}`}><Table.Td>{STAGE_LABELS[s.stage]}</Table.Td><Table.Td>{VARIANT_LABELS[v.variant_id]}</Table.Td><Table.Td>{s.status === "done" ? formatMs(s.duration_ms) : s.status}</Table.Td><Table.Td>{formatMs(s.quota_wait_ms)}</Table.Td></Table.Tr>
          )))}
        </Table.Tbody>
      </Table>
      <Stack gap={2} mt="sm">
        <Text size="xs" c="dimmed">Версии: приложение {result.versions.app}, контракты {result.versions.contracts ?? "—"}, модели {result.versions.models.map((x) => x.name).join(", ")}, рендерер {result.versions.renderer ? `${result.versions.renderer.name} ${result.versions.renderer.version}` : "—"}</Text>
        {result.warnings?.map((w) => <Text key={w.code} size="xs" c="orange">{w.message}</Text>)}
      </Stack>
    </div>
  );
}

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <Text size="xs" c="dimmed">{label}</Text>
      <Text fw={600}>{value}</Text>
      {hint && <Text size="xs" c="dimmed">{hint}</Text>}
    </div>
  );
}
