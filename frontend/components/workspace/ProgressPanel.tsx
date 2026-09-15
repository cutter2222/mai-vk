"use client";

import { Badge, Group, Progress, Stack, Text, ThemeIcon, Tooltip } from "@mantine/core";
import { IconCheck, IconClock, IconLoader2, IconX } from "@tabler/icons-react";

import { StatusBadge } from "@/components/common/StatusBadge";
import type { GenerationResult } from "@/lib/api/types";
import { formatMs, STAGE_LABELS, VARIANT_LABELS } from "@/lib/format";

const COMMON: Array<GenerationResult["stage"]> = ["analyze", "import", "story"];

/** Ход задания: общие этапы и этапы каждого варианта с временем. Действия отмены и повтора живут в шапке проекта. */
export function ProgressPanel({ result }: { result: GenerationResult }) {
  const terminal = ["succeeded", "needs_review", "failed", "canceled"].includes(result.status);
  const common = COMMON.map((s) => result.metrics.stages?.find((x) => x.stage === s));
  const stubLayers = Object.entries(result.execution_mode.layers).filter(([, v]) => v !== "real").map(([k]) => k);

  return (
    <div data-testid="progress-panel">
      <Group justify="space-between" mb={4} wrap="nowrap">
        <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
          <Text fw={600}>Задание</Text>
          <Text size="xs" c="dimmed" ff="monospace" truncate>{result.job_id}</Text>
        </Group>
        <StatusBadge status={result.status} size="xs" />
      </Group>
      <Group gap="xs" mb="xs">
        <Tooltip label={`Режим исполнения: ${result.execution_mode.mode}. Слои-заглушки: ${stubLayers.length ? stubLayers.join(", ") : "нет"}`} multiline w={320}>
          <Badge size="xs" color={result.execution_mode.mode === "real" ? "green" : "gray"} data-testid="execution-mode">
            {result.execution_mode.mode === "real" ? "Настоящая генерация" : `Заглушки: ${stubLayers.length} из ${Object.keys(result.execution_mode.layers).length} слоёв`}
          </Badge>
        </Tooltip>
        {result.partial && <Badge color="yellow" size="xs">частичный результат</Badge>}
      </Group>
      <Text size="xs" c="dimmed" mb={6}>{result.progress?.message ?? STAGE_LABELS[result.stage]}</Text>
      <Progress value={result.progress?.percent ?? 0} size="sm" animated={!terminal} mb="md" />
      {result.error && (
        <Text size="sm" c="red" mb="sm" data-testid="job-error">
          {result.error.message}{result.error.retryable ? " Можно повторить." : ""}
        </Text>
      )}
      <Stack gap={6} mb="md">
        {COMMON.map((s, i) => {
          const st = common[i];
          return (
            <Group key={s} gap="xs" wrap="nowrap" justify="space-between">
              <Group gap={6} wrap="nowrap">
                <ThemeIcon size={18} radius="xl" variant="light" color={st?.status === "done" ? "green" : st?.status === "running" ? "blue" : st?.status === "failed" ? "red" : "gray"}>
                  {bullet(st?.status)}
                </ThemeIcon>
                <Text size="sm">{STAGE_LABELS[s]}</Text>
              </Group>
              <Text size="xs" c="dimmed">{st?.status === "done" ? formatMs(st.duration_ms) + (st.cache_hit ? " · из кэша" : "") : st?.status === "running" ? "выполняется" : "ожидает"}</Text>
            </Group>
          );
        })}
      </Stack>
      <Stack gap={8}>
        {result.variants.map((v) => (
          <div key={v.variant_id} data-testid={`variant-progress-${v.variant_id}`}>
            <Group gap="xs" justify="space-between" wrap="nowrap">
              <Text size="sm" fw={500}>{VARIANT_LABELS[v.variant_id] ?? v.variant_id}</Text>
              <StatusBadge status={v.status} size="xs" />
            </Group>
            <Group gap={4} wrap="nowrap" mt={2}>
              {(v.stages ?? []).map((s, i) => (
                <Tooltip key={s.stage} label={`${STAGE_LABELS[s.stage]}: ${s.status === "done" ? formatMs(s.duration_ms) : s.status}${s.quota_wait_ms ? `, ожидание квоты ${formatMs(s.quota_wait_ms)}` : ""}`}>
                  <Text size="xs" c={s.status === "failed" ? "red" : s.status === "running" ? "blue" : s.status === "done" ? "dimmed" : "gray.4"} fw={s.status === "running" ? 600 : 400} style={{ whiteSpace: "nowrap" }}>
                    {i > 0 ? "· " : ""}{STAGE_LABELS[s.stage].toLowerCase()}
                  </Text>
                </Tooltip>
              ))}
            </Group>
          </div>
        ))}
      </Stack>
    </div>
  );
}

function bullet(status?: string) {
  if (status === "done") return <IconCheck size={12} />;
  if (status === "running") return <IconLoader2 size={12} />;
  if (status === "failed") return <IconX size={12} />;
  return <IconClock size={12} />;
}
