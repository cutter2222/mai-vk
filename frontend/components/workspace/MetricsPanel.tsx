"use client";

import { Group, SimpleGrid, Stack, Table, Text } from "@mantine/core";

import type { GenerationResult } from "@/lib/api/types";
import { formatMs, formatNumber, STAGE_LABELS, VARIANT_LABELS } from "@/lib/format";

export function MetricsPanel({ result }: { result: GenerationResult }) {
  const m = result.metrics;
  const tl = m.timeline;
  // Этапы общие и по вариантам одной таблицей; колонка ожидания квоты — только если ждали:
  // в узкой колонке чата лишняя колонка выталкивала таблицу за край.
  const rows = [
    ...(m.stages ?? []).map((stage, i) => ({ key: `c${i}`, stage, variant: "общий" })),
    ...result.variants.flatMap((v) =>
      (v.stages ?? []).map((stage, i) => ({ key: `${v.variant_id}${i}`, stage, variant: VARIANT_LABELS[v.variant_id] })),
    ),
  ];
  const quota = rows.some((r) => (r.stage.quota_wait_ms ?? 0) > 0);
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
      <div className="metrics-scroll">
        <Table verticalSpacing={4} withRowBorders={false} fz="xs" className="metrics-table">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Этап</Table.Th><Table.Th>Вариант</Table.Th><Table.Th>Время</Table.Th>
              {quota && <Table.Th>Квота</Table.Th>}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((r) => (
              <Table.Tr key={r.key}>
                <Table.Td>{STAGE_LABELS[r.stage.stage]}</Table.Td>
                <Table.Td>{r.variant}</Table.Td>
                <Table.Td>{r.stage.status === "done" ? formatMs(r.stage.duration_ms) : r.stage.status}</Table.Td>
                {quota && <Table.Td>{formatMs(r.stage.quota_wait_ms)}</Table.Td>}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </div>
      {result.warnings?.length ? (
        <Stack gap={2} mt="sm">
          {result.warnings.map((w) => <Text key={w.code} size="xs" c="orange">{w.message}</Text>)}
        </Stack>
      ) : null}
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
