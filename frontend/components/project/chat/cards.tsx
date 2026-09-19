"use client";

import { Accordion, Anchor, Badge, Button, ColorSwatch, Group, Loader, SimpleGrid, Stack, Text, Tooltip } from "@mantine/core";
import { IconFile, IconFileTypePpt, IconRocket } from "@tabler/icons-react";

import { SlideImage } from "@/components/common/SlideImage";
import { ProgressPanel } from "@/components/workspace/ProgressPanel";
import { MetricsPanel } from "@/components/workspace/MetricsPanel";
import { api, ApiError, TERMINAL_STATES, type ContentDetail, type TemplateDetail } from "@/lib/api/client";
import type { JobStatus } from "@/lib/api/types";
import { usePolling } from "@/lib/api/usePolling";
import { formatMs, STATUS_LABELS, VARIANT_LABELS } from "@/lib/format";
import { useElapsed } from "@/lib/hooks/useElapsed";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";
import type { BriefDraft, ChatMessage, PptxAnswer, Project } from "@/lib/state/projects";

import { PURPOSE_LABELS, PURPOSE_OPTIONS } from "../panels/BriefFields";

type Msg<K extends string> = Extract<ChatMessage, { kind: K }>;

export interface CardContext {
  project: Project;
  session: GenerationSession;
  onResolveTemplate: (messageId: string, fileId: string, answer: PptxAnswer) => void;
  onEditBrief: () => void;
  onSetPurpose: (purpose: BriefDraft["purpose"]) => void;
  onGenerate: () => void;
  generating: boolean;
  onOpenAudit: (variantId?: string) => void;
  onRepairAll: () => void;
  onRetryImport: () => void;
}

/** Обёртка карточки шага конвейера: заголовок, содержимое, действия. */
function Card({ title, aside, children, testId }: { title: React.ReactNode; aside?: React.ReactNode; children?: React.ReactNode; testId?: string }) {
  return (
    <div className="chat-card" data-testid={testId}>
      <Group justify="space-between" wrap="nowrap" mb={children ? 8 : 0}>
        <Text size="sm" fw={600}>{title}</Text>
        {aside}
      </Group>
      {children}
    </div>
  );
}

/** Вопрос о PPTX: шаблон оформления или материал. Один и тот же вид для файла в истории и для файла, который ещё грузится. */
export function PptxQuestion({ name, resolved, uploading, onAnswer, testId }: { name: string; resolved?: PptxAnswer; uploading?: boolean; onAnswer: (answer: PptxAnswer) => void; testId: string }) {
  const text = resolved === "template"
    ? uploading ? "Разберу как шаблон, как только файл загрузится." : "Разбираю как шаблон: палитра, шрифты и композиции слайдов."
    : resolved === "deck"
      ? uploading ? "Открою как готовую презентацию, как только файл загрузится." : "Открываю как готовую презентацию: слайды остаются как есть, править можно из чата."
      : resolved === "material"
        ? uploading ? "Считаю материалом: импортирую, как только файл загрузится." : "Считаю материалом: текст слайдов пойдёт в содержание."
        : "Похоже на презентацию. Что с ней сделать?";
  return (
    <Card title={<Group gap={6} wrap="nowrap"><IconFileTypePpt size={16} />{name}</Group>} aside={uploading ? <Loader size={12} /> : undefined} testId={testId}>
      <Text size="sm" c="dimmed" mb={resolved ? 0 : 8}>{text}</Text>
      {!resolved && (
        <Stack gap={6} align="stretch">
          <Button size="xs" variant="default" justify="flex-start" onClick={() => onAnswer("template")} data-testid="answer-template">Сделать шаблоном</Button>
          <Button size="xs" variant="default" justify="flex-start" onClick={() => onAnswer("deck")} data-testid="answer-deck">Использовать как готовую презентацию</Button>
          <Button size="xs" variant="default" justify="flex-start" onClick={() => onAnswer("material")} data-testid="answer-material">Использовать как материал</Button>
          <Text size="xs" c="dimmed">Шаблон — по нему собираются новые слайды; готовая презентация — слайды переносятся как есть, а править их можно из чата; материал — текст слайдов пойдёт в содержание.</Text>
        </Stack>
      )}
    </Card>
  );
}

export function TemplateQuestionCard({ m, ctx }: { m: Msg<"template_question">; ctx: CardContext }) {
  const file = ctx.project.files.find((f) => f.file_id === m.file_id);
  return <PptxQuestion name={file?.name ?? "файл"} resolved={m.resolved} onAnswer={(answer) => ctx.onResolveTemplate(m.event_id, m.file_id, answer)} testId={`template-question-${m.event_id}`} />;
}

export function TemplateCard({ m, ctx }: { m: Msg<"template_card">; ctx: CardContext }) {
  const detail = usePolling<TemplateDetail>(() => api.templates.get(m.template_id), (d) => d.status === "succeeded" || d.status === "failed", [m.template_id]);
  const profile = detail.data?.profile;
  const current = ctx.project.template_id === m.template_id;
  // Шаблон удалили из библиотеки: карточка остаётся в истории, но профиля у неё больше нет.
  const gone = detail.error instanceof ApiError && detail.error.status === 404;
  const timing = detail.data?.timing;
  const elapsed = useElapsed(timing?.started_at ?? timing?.created_at, profile ? (timing?.finished_at ?? null) : null);
  return (
    <Card
      title="Шаблон"
      aside={gone ? <Badge color="gray" size="xs">удалён из библиотеки</Badge> : current ? <Badge color="green" size="xs">используется</Badge> : <Badge color="gray" size="xs">заменён</Badge>}
      testId="template-card"
    >
      <Group gap="sm" wrap="nowrap" align="flex-start">
        <IconFileTypePpt size={22} stroke={1.5} style={{ flex: "0 0 auto", marginTop: 2 }} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <Text size="sm" fw={500} truncate>{detail.data?.name ?? m.template_id}</Text>
          {gone ? (
            <Text size="xs" c="dimmed" mt={4}>Шаблон удалён из библиотеки. Загрузите PPTX снова или выберите другой в шапке проекта.</Text>
          ) : !profile ? (
            <Group gap={6} mt={4}><Loader size={12} /><Text size="xs" c="dimmed">{detail.error ? detail.error.message : `Анализирую образцы, палитру и шрифты${elapsed != null ? ` · ${formatMs(elapsed)}` : ""}. Ждать не нужно, можно добавлять материалы.`}</Text></Group>
          ) : (
            <Stack gap={6} mt={4}>
              <Text size="xs" c="dimmed" data-testid="template-profile">{profile.patterns.length} композиций · {profile.stats.slides} слайдов · {profile.design_tokens.typography.fonts.slice(0, 2).map((f) => f.family).join(", ")}{timing?.duration_ms != null ? ` · анализ ${formatMs(timing.duration_ms)}` : ""}</Text>
              <Group gap={4}>
                {profile.design_tokens.colors.palette.slice(0, 8).map((c) => (
                  <Tooltip key={c.hex} label={`${c.hex} · ${c.role}`}><ColorSwatch color={c.hex} size={14} /></Tooltip>
                ))}
              </Group>
              <Text size="xs" c="dimmed">
                Из этих композиций будут собраны слайды; сменить шаблон можно в шапке проекта.{" "}
                <Anchor href={`/templates?id=${encodeURIComponent(m.template_id)}`} target="_blank" rel="noreferrer" size="xs" data-testid="template-open-library">Что извлечено из шаблона</Anchor>
              </Text>
            </Stack>
          )}
        </div>
      </Group>
    </Card>
  );
}

export function ContentCard({ m, ctx }: { m: Msg<"content_card">; ctx: CardContext }) {
  const detail = usePolling<ContentDetail>(() => api.content.get(m.package_id), (d) => d.status === "succeeded" || d.status === "failed", [m.package_id]);
  const pkg = detail.data?.package;
  // Импорт упал: карточка говорит об этом, а не крутит «извлекаю» бесконечно.
  const failed = detail.data?.status === "failed" ? (detail.data.error?.message ?? "импорт не удался") : detail.error ? detail.error.message : null;
  const files = ctx.project.files.filter((f) => m.file_ids.includes(f.file_id));
  const current = ctx.project.package_id === m.package_id;
  return (
    <Card title="Материалы" aside={!current ? <Badge color="gray" size="xs">переимпортированы</Badge> : undefined} testId="content-card">
      {files.length > 0 && (
        <Group gap={6} mb={6}>
          {files.map((f) => (
            <Badge key={f.file_id} color="gray" leftSection={<IconFile size={11} />} size="sm">{f.name}</Badge>
          ))}
        </Group>
      )}
      {!pkg ? (
        failed ? (
          <Stack gap={6} align="flex-start">
            <Text size="xs" c="red" data-testid="import-error">Материалы не импортированы: {failed.split("\n")[0]}</Text>
            {current && <Button size="xs" variant="default" onClick={ctx.onRetryImport} data-testid="import-retry">Повторить импорт</Button>}
          </Stack>
        ) : (
          <Group gap={6}><Loader size={12} /><Text size="xs" c="dimmed">Извлекаю блоки, факты, таблицы и изображения.</Text></Group>
        )
      ) : (
        <Text size="sm" data-testid="import-summary">
          Нашёл {pkg.blocks.length} блоков, {pkg.facts.length} фактов, {pkg.datasets.length} таблиц, {pkg.assets.length} изображений{files.length === 0 ? " — по брифу" : ""}.
          {pkg.missing_data && pkg.missing_data.length > 0 ? ` Не хватает: ${pkg.missing_data.map((x) => x.what).join(", ")} — выдумывать не буду.` : ""}
        </Text>
      )}
    </Card>
  );
}

export function BriefCard({ m, ctx }: { m: Msg<"brief_card">; ctx: CardContext }) {
  const { brief, settings } = ctx.project;
  const missingPurpose = !brief.purpose;
  const missing = [!ctx.project.template_id ? "шаблон" : null, !ctx.project.package_id ? "материалы или бриф" : null, missingPurpose ? "назначение" : null].filter(Boolean) as string[];
  const ready = missing.length === 0;
  const hl = (field: string) => (m.understood.includes(field) ? { fw: 500 } : {});
  const slides = settings.mode === "exact" ? `ровно ${settings.exact}` : `${settings.min}–${settings.max}`;
  const rows: Array<[string, string, string]> = [
    ["purpose", "Назначение", brief.purpose ? PURPOSE_LABELS[brief.purpose] ?? brief.purpose : "—"],
    ["title", "Тема", brief.title || "—"],
    ["audience", "Аудитория", brief.audience || "—"],
    ["goal", "Цель", brief.goal || "—"],
  ];
  return (
    <Card title={m.understood.length ? "Понял задачу так" : "Задача"} aside={<Button size="compact-xs" variant="subtle" color="gray" onClick={ctx.onEditBrief} data-testid="edit-brief">Изменить</Button>} testId="brief-card">
      <div className="brief-grid">
        {rows.map(([key, label, value]) => (
          <div key={key} className="brief-row">
            <Text size="xs" c="dimmed">{label}</Text>
            <Text size="sm" {...hl(key)}>{value}</Text>
          </div>
        ))}
        <div className="brief-row">
          <Text size="xs" c="dimmed">Объём</Text>
          <Text size="sm" {...hl("slide_count")}>{slides} слайдов · {settings.variants.length === 3 ? "три варианта" : settings.variants.map((v) => VARIANT_LABELS[v]?.toLowerCase()).join(", ")}{settings.contextual ? "" : " · без контекстного аудита"}</Text>
        </div>
      </div>
      {missingPurpose && (
        <Stack gap={6} mt={8}>
          <Text size="sm">Уточните назначение — от него зависит структура колоды:</Text>
          <Group gap={6}>
            {PURPOSE_OPTIONS.map((o) => (
              <Button key={o.value} size="xs" variant="default" onClick={() => ctx.onSetPurpose(o.value as BriefDraft["purpose"])} data-testid={`purpose-${o.value}`}>{o.label}</Button>
            ))}
          </Group>
        </Stack>
      )}
      {m.understood.length > 0 && m.brief_source && (
        <Text size="xs" c="dimmed" mt={6} data-testid="brief-source">
          {m.brief_source === "model" ? "Поля из сообщения выделила модель; проверьте и поправьте при необходимости." : "Поля из сообщения выделены по правилам: модель была недоступна."}
        </Text>
      )}
      <Stack gap={6} mt={10}>
        <Button size="sm" leftSection={<IconRocket size={15} />} disabled={!ready} loading={ctx.generating} onClick={ctx.onGenerate} data-testid="generate" w="fit-content">
          {ctx.project.job_id ? "Сгенерировать заново" : "Сгенерировать"}
        </Button>
        <Text size="xs" c="dimmed">{ready ? "Три варианта строятся параллельно, слайды появятся справа." : `Не хватает: ${missing.join(", ")}.`}</Text>
      </Stack>
    </Card>
  );
}

export function JobCard({ m, ctx }: { m: Msg<"job_card">; ctx: CardContext }) {
  const { session } = ctx;
  if (session.jobId !== m.job_id) {
    return <Card title="Генерация" aside={<Badge color="gray" size="xs">заменена новым заданием</Badge>} />;
  }
  if (!session.result) {
    return <Card title="Генерация"><Group gap={6}><Loader size={12} /><Text size="xs" c="dimmed">{session.job.error ? session.job.error.message : "Ставлю задание в очередь."}</Text></Group></Card>;
  }
  return (
    <Card title="Генерация" testId="job-card">
      <ProgressPanel result={session.result} />
      {session.terminal && (
        <Accordion variant="default" chevronPosition="left" mt={6} styles={{ control: { paddingLeft: 0, paddingRight: 0 }, content: { paddingLeft: 0, paddingRight: 0 }, item: { border: 0 } }}>
          <Accordion.Item value="metrics">
            <Accordion.Control><Text size="xs" c="dimmed">Метрики и версии</Text></Accordion.Control>
            <Accordion.Panel><MetricsPanel result={session.result} /></Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      )}
    </Card>
  );
}

export function AuditCard({ m, ctx }: { m: Msg<"audit_card">; ctx: CardContext }) {
  const { session } = ctx;
  if (session.jobId !== m.job_id || !session.result) return null;
  const variants = session.result.variants;
  const total = variants.reduce((n, v) => n + (v.audit?.issues_total ?? 0), 0);
  const worst = [...variants].sort((a, b) => (b.audit?.issues_total ?? 0) - (a.audit?.issues_total ?? 0))[0];
  const incomplete = variants.some((v) => v.audit && !v.audit.coverage_complete);
  return (
    <Card title="Аудит" aside={<Badge color={total ? "yellow" : "green"} size="xs">{total ? `${total} находок` : "находок нет"}</Badge>} testId="audit-card">
      <Stack gap={4} mb={8}>
        {variants.map((v) => (
          <Group key={v.variant_id} justify="space-between" wrap="nowrap">
            <Text size="sm">{VARIANT_LABELS[v.variant_id] ?? v.variant_id}</Text>
            <Text size="xs" c="dimmed">{v.status === "failed" ? STATUS_LABELS.failed : v.audit ? `${v.audit.issues_total} находок${v.audit.coverage_complete ? "" : " · аудит неполный"}` : "—"}</Text>
          </Group>
        ))}
      </Stack>
      <Text size="xs" c="dimmed" mb={8}>
        {total ? "Находки отмечены рамками на слайдах и красными точками в ленте. Выберите, что исправить: каждое исправление создаёт новую ревизию." : "Все проверки пройдены."}
        {incomplete ? " Часть проверок не выполнена, поэтому статус «требует проверки»." : ""}
      </Text>
      <Group gap="xs">
        <Button size="xs" variant="default" onClick={() => ctx.onOpenAudit(worst?.variant_id)} data-testid="open-audit">Показать находки</Button>
        {total > 0 && <Button size="xs" onClick={ctx.onRepairAll} data-testid="repair-all">Исправить всё исправимое</Button>}
      </Group>
    </Card>
  );
}

/**
 * Правка слайда по запросу: ход задания, затем «до/после» и что изменено; отказ — с причиной.
 * Карточка опрашивает своё задание сама, результат читает из живого GenerationResult по идентификатору.
 */
export function EditCard({ m, ctx }: { m: Msg<"edit_card">; ctx: CardContext }) {
  const { session } = ctx;
  const entry = session.result?.edits?.find((e) => e.edit_job_id === m.edit_job_id);
  const settled = Boolean(entry && entry.result !== undefined);
  const status = usePolling<JobStatus>(!settled ? () => api.jobs.get(m.edit_job_id) : null, (s) => TERMINAL_STATES.has(s.status), [m.edit_job_id, settled]);
  const title = `Правка слайда ${m.slide_index + 1}`;
  const variantLabel = VARIANT_LABELS[m.variant_id] ?? m.variant_id;
  if (session.jobId !== m.job_id) {
    return <Card title={title} aside={<Badge color="gray" size="xs">задание заменено</Badge>} />;
  }
  const failed = status.data?.status === "failed" || entry?.result === "failed";
  if (failed) {
    const message = entry?.message ?? status.data?.error?.message ?? "Правка не выполнена";
    return (
      <Card title={title} aside={<Badge color="red" size="xs">не применена</Badge>} testId="edit-card">
        <Text size="sm">{message}</Text>
        <Text size="xs" c="dimmed" mt={4}>Выберите слайд слева и повторите просьбу другими словами.</Text>
      </Card>
    );
  }
  if (entry?.result === "unchanged" || (status.data?.status === "succeeded" && status.data.result?.unchanged)) {
    const reason = entry?.change_note ?? status.data?.result?.change_note ?? "";
    return (
      <Card title={title} aside={<Badge color="gray" size="xs">без изменений</Badge>} testId="edit-card">
        <Text size="sm" data-testid="edit-reason">Оставил слайд как есть: {reason || "просьбу выполнить нельзя"}</Text>
        <Text size="xs" c="dimmed" mt={4}>Данные не выдумываются: добавьте материалы или уточните просьбу.</Text>
      </Card>
    );
  }
  if (entry?.result === "applied" && entry.new_revision) {
    const name = (rev: number) => `${m.variant_id}/r${rev}/thumbs/slide-${String(m.slide_index + 1).padStart(2, "0")}.png`;
    const show = () => {
      session.setSelectedVariant(m.variant_id);
      session.setRevision(null);
      session.setLayout("single");
      session.selectSlide(m.slide_index);
    };
    return (
      <Card title={title} aside={<Badge color="green" size="xs">ревизия {entry.new_revision}</Badge>} testId="edit-card">
        <Text size="sm" mb={8} data-testid="edit-note">{entry.change_note || "Слайд переделан по просьбе"}</Text>
        <SimpleGrid cols={2} spacing="xs" mb={8}>
          <div>
            <SlideImage src={api.generations.artifactUrl(m.job_id, name(entry.base_revision))} alt="до" />
            <Text size="xs" ta="center" c="dimmed">до · r{entry.base_revision}</Text>
          </div>
          <div>
            <SlideImage src={api.generations.artifactUrl(m.job_id, name(entry.new_revision))} alt="после" />
            <Text size="xs" ta="center" c="dimmed">после · r{entry.new_revision}</Text>
          </div>
        </SimpleGrid>
        <Group gap="xs">
          <Button size="xs" variant="default" onClick={show} data-testid="edit-show">Показать слайд</Button>
          <Text size="xs" c="dimmed">{variantLabel}: остальные слайды не менялись, прежняя ревизия доступна в панели ревизий.</Text>
        </Group>
      </Card>
    );
  }
  const message = status.data?.progress?.message ?? `Переделываю слайд ${m.slide_index + 1}`;
  return (
    <Card title={title} aside={<Loader size={12} />} testId="edit-card">
      <Text size="xs" c="dimmed">{status.error ? status.error.message : `${message}: план → сборка → экспорт → проверка.`}</Text>
    </Card>
  );
}
