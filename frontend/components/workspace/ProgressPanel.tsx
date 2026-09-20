"use client";

import { Badge, Group, Loader, Text, Tooltip } from "@mantine/core";

import { StatusBadge } from "@/components/common/StatusBadge";
import type { GenerationResult } from "@/lib/api/types";
import { formatMs, STAGE_LABELS, VARIANT_LABELS } from "@/lib/format";
import { useElapsed } from "@/lib/hooks/useElapsed";

const COMMON: Array<GenerationResult["stage"]> = ["analyze", "import", "story"];

/**
 * Ход задания: строка состояния, общие этапы, этапы каждого варианта.
 *
 * Карточка живёт в узкой панели чата, поэтому в ней ровно три уровня: что происходит сейчас,
 * из чего это состоит и служебная подпись внизу. Идентификатор задания и режим исполнения
 * ушли из шапки в подпись: они нужны при разборе, а не при чтении. Вместо процентов —
 * секунды с начала, после завершения — итог. Отмена и повтор живут в шапке проекта.
 */
export function ProgressPanel({ result }: { result: GenerationResult }) {
  const terminal = ["succeeded", "needs_review", "failed", "canceled"].includes(result.status);
  const elapsed = useElapsed(result.created_at, terminal ? (result.finished_at ?? result.created_at) : null);
  const total = terminal ? (result.metrics.totals?.duration_ms ?? elapsed) : elapsed;
  const common = COMMON.map((s) => result.metrics.stages?.find((x) => x.stage === s));
  const layers = Object.keys(result.execution_mode.layers).length;
  const stubLayers = Object.entries(result.execution_mode.layers).filter(([, v]) => v !== "real").map(([k]) => k);

  return (
    <div data-testid="progress-panel">
      <div className="job-line" data-testid="job-elapsed">
        {/* Сообщение переносится по строкам, а не обрезается: «Готово: 3 варианта, аудит
            завершён у 3» без хвоста читается как «Готово: 3 варианта, аудит за…». */}
        <Group gap={7} wrap="nowrap" align="flex-start" style={{ minWidth: 0 }}>
          {!terminal && <Loader size={12} mt={3} />}
          <Text size="sm" lineClamp={2}>{result.progress?.message ?? STAGE_LABELS[result.stage]}</Text>
        </Group>
        <Group gap={8} wrap="nowrap" align="flex-start" style={{ flex: "0 0 auto" }}>
          <Text size="xs" c="dimmed" mt={2} style={{ whiteSpace: "nowrap" }}>{terminal ? `за ${formatMs(total)}` : formatMs(total)}</Text>
          <StatusBadge status={result.status} size="xs" />
        </Group>
      </div>

      {/* Список пропущенных слайдов бывает в десяток номеров и занимал в карточке три строки:
          две видно, остальное — в подсказке. */}
      {result.warnings?.filter((w) => w.code === "original_slides_skipped").map((w) => (
        <Text key={w.code} size="xs" c="dimmed" mt={8} lineClamp={2} title={w.message} data-testid="job-warning">{w.message}</Text>
      ))}
      {result.error && (
        <Text size="sm" c="red" mt={8} data-testid="job-error">
          {result.error.message}{result.error.retryable ? " Можно повторить." : ""}
        </Text>
      )}

      <div className="job-stages">
        {COMMON.map((s, i) => {
          const st = common[i];
          return (
            <div key={s} className="job-stage" data-status={st?.status ?? "pending"}>
              <i />
              <span>{STAGE_LABELS[s]}</span>
              <b>{st?.status === "done" ? formatMs(st.duration_ms) + (st.cache_hit ? " · из кэша" : "") : st?.status === "running" ? "выполняется" : "ожидает"}</b>
            </div>
          );
        })}
      </div>

      <div className="job-variants">
        {result.variants.map((v) => (
          <div key={v.variant_id} className="job-variant" data-testid={`variant-progress-${v.variant_id}`}>
            <Group gap="xs" justify="space-between" wrap="nowrap">
              <Text size="sm" fw={500} truncate>{VARIANT_LABELS[v.variant_id] ?? v.variant_id}</Text>
              <StatusBadge status={v.status} size="xs" />
            </Group>
            <Group gap={5} wrap="wrap" mt={3}>
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
      </div>

      <div className="job-foot">
        <Text size="xs" c="dimmed" ff="monospace" truncate title={result.job_id}>{result.job_id}</Text>
        <Tooltip label={`Режим исполнения: ${result.execution_mode.mode}. Слои-заглушки: ${stubLayers.length ? stubLayers.join(", ") : "нет"}`} multiline w={320}>
          <Text size="xs" c={result.execution_mode.mode === "real" ? "dimmed" : "orange"} style={{ whiteSpace: "nowrap" }} data-testid="execution-mode">
            {result.execution_mode.mode === "real" ? "настоящая генерация" : `Заглушки: ${stubLayers.length} из ${layers} слоёв`}
          </Text>
        </Tooltip>
        {result.partial && <Badge color="ink" variant="light" size="xs">частичный результат</Badge>}
      </div>
    </div>
  );
}
