"use client";

import { Badge, Button, Card, Group, Progress, Stack, Text, Timeline, Tooltip } from "@mantine/core";
import { IconCheck, IconClock, IconLoader2, IconPlayerStop, IconRefresh, IconX } from "@tabler/icons-react";

import { StatusBadge } from "@/components/common/StatusBadge";
import type { GenerationResult } from "@/lib/api/types";
import { formatMs, STAGE_LABELS, VARIANT_LABELS } from "@/lib/format";

interface Props {
  result: GenerationResult;
  onCancel: () => void;
  onRetry: () => void;
  busy: boolean;
}

const COMMON: Array<GenerationResult["stage"]> = ["analyze", "import", "story"];

export function ProgressPanel({ result, onCancel, onRetry, busy }: Props) {
  const terminal = ["succeeded", "needs_review", "failed", "canceled"].includes(result.status);
  const common = COMMON.map((s) => result.metrics.stages?.find((x) => x.stage === s));
  const stubLayers = Object.entries(result.execution_mode.layers).filter(([, v]) => v !== "real").map(([k]) => k);

  return (
    <Card data-testid="progress-panel">
      <Group justify="space-between" align="flex-start" mb="sm">
        <div>
          <Group gap="sm">
            <Text fw={600}>Задание {result.job_id}</Text>
            <StatusBadge status={result.status} />
            {result.partial && <Badge color="yellow" variant="light">частичный результат</Badge>}
          </Group>
          <Text size="sm" c="dimmed">{result.progress?.message ?? STAGE_LABELS[result.stage]}</Text>
        </div>
        <Group gap="xs">
          <Tooltip label={`Режим исполнения: ${result.execution_mode.mode}. Слои-заглушки: ${stubLayers.length ? stubLayers.join(", ") : "нет"}`} withArrow multiline w={320}>
            <Badge variant="light" color={result.execution_mode.mode === "real" ? "green" : "orange"} data-testid="execution-mode">
              {result.execution_mode.mode === "real" ? "Настоящая генерация" : `Заглушки: ${stubLayers.length} из ${Object.keys(result.execution_mode.layers).length} слоёв`}
            </Badge>
          </Tooltip>
          {!terminal && (
            <Button variant="light" color="red" size="xs" leftSection={<IconPlayerStop size={14} />} onClick={onCancel} loading={busy} data-testid="cancel">
              Отменить
            </Button>
          )}
          {(result.status === "failed" || result.status === "canceled" || result.partial) && (
            <Button variant="light" size="xs" leftSection={<IconRefresh size={14} />} onClick={onRetry} loading={busy} data-testid="retry">
              Повторить
            </Button>
          )}
        </Group>
      </Group>
      <Progress value={result.progress?.percent ?? 0} size="sm" animated={!terminal} mb="md" />
      {result.error && (
        <Text size="sm" c="red" mb="sm" data-testid="job-error">
          {result.error.message}{result.error.retryable ? " Можно повторить." : ""}
        </Text>
      )}
      <Group align="flex-start" gap="xl" wrap="nowrap">
        <Timeline active={common.filter((s) => s?.status === "done").length} bulletSize={18} lineWidth={2} style={{ minWidth: 220 }}>
          {COMMON.map((s, i) => {
            const st = common[i];
            return (
              <Timeline.Item key={s} bullet={bullet(st?.status)} title={<Text size="sm">{STAGE_LABELS[s]}</Text>}>
                <Text size="xs" c="dimmed">{st?.status === "done" ? formatMs(st.duration_ms) + (st.cache_hit ? " · из кэша" : "") : st?.status === "running" ? "выполняется" : "ожидает"}</Text>
              </Timeline.Item>
            );
          })}
        </Timeline>
        <Stack gap="xs" style={{ flex: 1 }}>
          {result.variants.map((v) => (
            <Group key={v.variant_id} gap="sm" wrap="nowrap" data-testid={`variant-progress-${v.variant_id}`}>
              <Text size="sm" w={140} fw={500}>{VARIANT_LABELS[v.variant_id] ?? v.variant_id}</Text>
              {(v.stages ?? []).map((s) => (
                <Tooltip key={s.stage} label={`${STAGE_LABELS[s.stage]}: ${s.status === "done" ? formatMs(s.duration_ms) : s.status}${s.quota_wait_ms ? `, ожидание квоты ${formatMs(s.quota_wait_ms)}` : ""}`} withArrow>
                  <Badge variant={s.status === "done" ? "light" : s.status === "running" ? "filled" : "outline"} color={s.status === "failed" ? "red" : s.status === "done" ? "green" : s.status === "running" ? "blue" : "gray"} size="sm">
                    {STAGE_LABELS[s.stage]}
                  </Badge>
                </Tooltip>
              ))}
              <StatusBadge status={v.status} />
            </Group>
          ))}
        </Stack>
      </Group>
    </Card>
  );
}

function bullet(status?: string) {
  if (status === "done") return <IconCheck size={12} />;
  if (status === "running") return <IconLoader2 size={12} />;
  if (status === "failed") return <IconX size={12} />;
  return <IconClock size={12} />;
}
