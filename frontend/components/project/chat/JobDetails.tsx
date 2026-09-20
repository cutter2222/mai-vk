"use client";

import { Group, Text, Tooltip } from "@mantine/core";

import { MetricsPanel } from "@/components/workspace/MetricsPanel";
import type { GenerationResult } from "@/lib/api/types";
import { formatMs, STAGE_LABELS } from "@/lib/format";

const COMMON: Array<GenerationResult["stage"]> = ["analyze", "import", "story"];

/**
 * Служебное о задании: общие этапы со временем, идентификатор, режим исполнения и метрики.
 *
 * В ленте этого не видно: там задание говорит одной фразой. Здесь лежит то, что нужно при
 * разборе — почему долго, что взято из кэша, какие слои работали заглушками, за какой job_id
 * спрашивать в логах. Раскрывается по требованию и не занимает места, пока не спросили.
 */
export function JobDetails({ result }: { result: GenerationResult }) {
  const common = COMMON.map((s) => result.metrics.stages?.find((x) => x.stage === s));
  const layers = Object.keys(result.execution_mode.layers).length;
  const stubs = Object.entries(result.execution_mode.layers).filter(([, v]) => v !== "real").map(([k]) => k);

  return (
    <div className="chat-details" data-testid="job-details-body">
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

      <Group gap={10} mt={10} mb={12} wrap="nowrap" data-testid="job-meta">
        <Text size="xs" c="dimmed" ff="monospace" truncate title={result.job_id}>{result.job_id}</Text>
        <Tooltip label={`Режим исполнения: ${result.execution_mode.mode}. Слои-заглушки: ${stubs.length ? stubs.join(", ") : "нет"}`} multiline w={320}>
          <Text size="xs" c={result.execution_mode.mode === "real" ? "dimmed" : "orange"} style={{ whiteSpace: "nowrap" }} data-testid="execution-mode">
            {result.execution_mode.mode === "real" ? "настоящая генерация" : `Заглушки: ${stubs.length} из ${layers} слоёв`}
          </Text>
        </Tooltip>
      </Group>

      <MetricsPanel result={result} />
    </div>
  );
}
