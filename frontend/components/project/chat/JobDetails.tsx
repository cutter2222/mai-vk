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
      <Versions versions={result.versions} />
    </div>
  );
}

/**
 * Чем сделан результат (29.09.2026): модель и версии скиллов задания. Скиллы и промпты
 * версионируются в репозитории (`skills/<name>/skill.yaml`); по этому списку видно, что
 * новая версия скилла дала другой результат.
 */
function Versions({ versions }: { versions?: GenerationResult["versions"] }) {
  if (!versions) return null;
  const models = versions.models ?? [];
  const skills = [...(versions.skills ?? [])].sort((a, b) => a.name.localeCompare(b.name));
  return (
    <div className="job-versions" data-testid="job-versions">
      <Text size="xs" fw={600} c="dimmed" tt="uppercase" mt={12} mb={6}>Версии</Text>
      {models.map((m) => (
        <Text key={`${m.role}-${m.name}`} size="xs" data-testid={`job-model-${m.role}`}>
          <Text span size="xs" c="dimmed">{m.role === "vlm" ? "зрение" : m.role === "llm" ? "модель" : m.role}: </Text>
          {m.name}{m.params_b ? ` · ${m.params_b}B` : ""}{m.license ? ` · ${m.license}` : ""}
        </Text>
      ))}
      <div className="job-version-grid">
        {skills.map((sk) => (
          <span key={sk.name} data-testid={`job-skill-${sk.name}`}><i>{sk.name}</i><b>{sk.version}</b></span>
        ))}
      </div>
      <Text size="xs" c="dimmed" mt={6}>
        {[`приложение ${versions.app}`, versions.contracts ? `контракты ${versions.contracts}` : null,
          versions.analyzer ? `анализатор ${versions.analyzer.version}` : null,
          versions.renderer ? `рендерер ${versions.renderer.version}` : null].filter(Boolean).join(" · ")}
      </Text>
    </div>
  );
}
