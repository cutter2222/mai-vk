"use client";

import { Accordion, Alert, Badge, Button, Card, Checkbox, Group, Loader, Progress, ScrollArea, Stack, Text, Tooltip } from "@mantine/core";
import { IconAlertTriangle, IconCircleCheck, IconTool } from "@tabler/icons-react";

import type { AuditReport } from "@/lib/api/types";
import { CATEGORY_LABELS, SEVERITY_LABELS } from "@/lib/format";

type Issue = AuditReport["issues"][number];

interface Props {
  report: AuditReport | null;
  loading: boolean;
  stale: boolean;
  selected: Set<string>;
  activeIssue: string | null;
  onToggle: (id: string) => void;
  onFocus: (issue: Issue) => void;
  onRepair: () => void;
  repairing: boolean;
}

const SEVERITY_COLOR: Record<string, string> = { blocking: "red", error: "red", warning: "yellow", info: "blue" };
const COST_LABEL: Record<string, string> = { cheap: "без модели", llm: "с моделью", rerender: "с повторным рендером" };

export function AuditPanel({ report, loading, stale, selected, activeIssue, onToggle, onFocus, onRepair, repairing }: Props) {
  if (loading && !report) {
    return (
      <Card>
        <Group gap="sm"><Loader size="sm" /><Text size="sm">Загружаем отчёт аудита</Text></Group>
      </Card>
    );
  }
  if (!report) {
    return (
      <Card>
        <Text size="sm" c="dimmed">Аудит появится, когда вариант будет собран и отрендерен.</Text>
      </Card>
    );
  }

  const total = report.results.length;
  const passed = report.results.filter((r) => r.outcome === "passed").length;
  const failed = report.results.filter((r) => r.outcome === "failed").length;
  const notChecked = report.coverage.not_checked ?? 0;
  const notApplicable = report.coverage.not_applicable ?? 0;
  const bySlide = new Map<number, Issue[]>();
  report.issues.forEach((i) => bySlide.set(i.slide_index, [...(bySlide.get(i.slide_index) ?? []), i]));
  const fixable = report.issues.filter((i) => i.fix.available);
  const selectedCount = [...selected].filter((id) => fixable.some((i) => i.issue_id === id)).length;

  return (
    <Card data-testid="audit-panel">
      <Group justify="space-between" mb="xs">
        <Group gap="xs">
          <Text fw={600}>Аудит</Text>
          <Badge variant="light" color="gray">ревизия {report.revision}</Badge>
          {stale && <Badge color="orange" variant="light">устарел: есть новая ревизия</Badge>}
        </Group>
        <Tooltip label={report.coverage.complete ? "Все обязательные проверки выполнены" : `Не выполнено: ${report.coverage.missing_inputs?.join(", ") || "часть проверок"}`} withArrow>
          <Badge color={report.coverage.complete ? "green" : "orange"} variant="light" data-testid="audit-coverage">
            {report.coverage.complete ? "покрытие полное" : "покрытие неполное"}
          </Badge>
        </Tooltip>
      </Group>

      <Group gap="lg" mb="xs">
        <Stat label="пройдено" value={passed} color="green" />
        <Stat label="нарушений" value={failed} color="red" />
        <Stat label="не проверено" value={notChecked} color="orange" />
        <Stat label="неприменимо" value={notApplicable} color="gray" />
        <Text size="xs" c="dimmed">всего результатов: {total}, оценка {report.summary.score ?? "—"}</Text>
      </Group>
      <Progress.Root size="sm" mb="md">
        <Progress.Section value={(passed / Math.max(total, 1)) * 100} color="green" />
        <Progress.Section value={(failed / Math.max(total, 1)) * 100} color="red" />
        <Progress.Section value={(notChecked / Math.max(total, 1)) * 100} color="orange" />
        <Progress.Section value={(notApplicable / Math.max(total, 1)) * 100} color="gray" />
      </Progress.Root>

      {report.issues.length === 0 ? (
        <Alert color="green" icon={<IconCircleCheck size={16} />}>Находок нет. {report.coverage.complete ? "Аудит полный." : "Но часть проверок не выполнена, поэтому статус «требует проверки»."}</Alert>
      ) : (
        <>
          <ScrollArea.Autosize mah={420}>
            <Accordion multiple defaultValue={[...bySlide.keys()].map(String)} variant="separated" chevronPosition="left">
              {[...bySlide.entries()].sort((a, b) => a[0] - b[0]).map(([slideIndex, issues]) => (
                <Accordion.Item key={slideIndex} value={String(slideIndex)}>
                  <Accordion.Control>
                    <Group gap="xs">
                      <Text size="sm" fw={500}>Слайд {slideIndex + 1}</Text>
                      <Badge size="xs" variant="light">{issues.length}</Badge>
                    </Group>
                  </Accordion.Control>
                  <Accordion.Panel>
                    <Stack gap="xs">
                      {issues.map((issue) => (
                        <Group
                          key={issue.issue_id}
                          align="flex-start"
                          wrap="nowrap"
                          gap="sm"
                          p={6}
                          style={{ borderRadius: 6, background: activeIssue === issue.issue_id ? "var(--mantine-color-blue-0)" : undefined, cursor: "pointer" }}
                          onClick={() => onFocus(issue)}
                          data-testid={`issue-${issue.issue_id}`}
                        >
                          <Checkbox
                            checked={selected.has(issue.issue_id)}
                            disabled={!issue.fix.available || stale}
                            onChange={() => onToggle(issue.issue_id)}
                            onClick={(e) => e.stopPropagation()}
                            aria-label="Выбрать для исправления"
                            data-testid={`issue-check-${issue.issue_id}`}
                          />
                          <div style={{ flex: 1 }}>
                            <Group gap={6} mb={2}>
                              <Badge size="xs" color={SEVERITY_COLOR[issue.severity]} variant="filled">{SEVERITY_LABELS[issue.severity]}</Badge>
                              <Badge size="xs" variant="outline" color="gray">{CATEGORY_LABELS[issue.check_id.split(".")[0]] ?? issue.check_id}</Badge>
                              <Badge size="xs" variant="light" color={issue.kind === "deterministic" ? "teal" : "grape"}>{issue.kind === "deterministic" ? "детерминированная" : "контекстная"}</Badge>
                              {issue.origin === "template" && <Badge size="xs" variant="light" color="gray">дефект шаблона</Badge>}
                            </Group>
                            <Text size="sm">{issue.message}</Text>
                            {issue.evidence?.details && <Text size="xs" c="dimmed">{issue.evidence.details}</Text>}
                            <Group gap={6} mt={4}>
                              {issue.fix.available ? (
                                <Text size="xs" c="dimmed"><IconTool size={12} style={{ verticalAlign: -2 }} /> {issue.fix.description} · {COST_LABEL[issue.fix.cost ?? "cheap"]}</Text>
                              ) : (
                                <Text size="xs" c="dimmed"><IconAlertTriangle size={12} style={{ verticalAlign: -2 }} /> автоматическое исправление недоступно</Text>
                              )}
                            </Group>
                          </div>
                        </Group>
                      ))}
                    </Stack>
                  </Accordion.Panel>
                </Accordion.Item>
              ))}
            </Accordion>
          </ScrollArea.Autosize>
          <Group mt="md" justify="space-between">
            <Text size="xs" c="dimmed">Выбрано {selectedCount} из {fixable.length} исправимых. Исправление создаст новую ревизию и перепроверит затронутые слайды и их соседей.</Text>
            <Button size="sm" disabled={selectedCount === 0 || stale} loading={repairing} onClick={onRepair} data-testid="repair">
              Исправить выбранное
            </Button>
          </Group>
        </>
      )}
    </Card>
  );
}

function Stat({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <Group gap={4}>
      <Text fw={600} c={color}>{value}</Text>
      <Text size="xs" c="dimmed">{label}</Text>
    </Group>
  );
}
