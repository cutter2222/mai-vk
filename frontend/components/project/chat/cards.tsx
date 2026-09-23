"use client";

import { Anchor, Button, ColorSwatch, Group, Progress, SimpleGrid, Stack, Text, Tooltip } from "@mantine/core";
import { IconCircleCheck, IconClock, IconRocket } from "@tabler/icons-react";
import { useState } from "react";

import { SlideImage } from "@/components/common/SlideImage";
import { api, ApiError, TERMINAL_STATES, type ContentDetail, type TemplateDetail } from "@/lib/api/client";
import type { GenerationResult, JobStatus } from "@/lib/api/types";
import { usePolling } from "@/lib/api/usePolling";
import { formatMs, plural, STAGE_LABELS, VARIANT_LABELS } from "@/lib/format";
import { useElapsed } from "@/lib/hooks/useElapsed";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";
import { slideCountAsked, type BriefDraft, type ChatMessage, type PptxAnswer, type Project } from "@/lib/state/projects";

import { PURPOSE_LABELS, PURPOSE_OPTIONS } from "../panels/BriefFields";
import progressStyles from "../preview/GenerationProgress.module.css";
import { JobDetails } from "./JobDetails";
import { SlideCompareModal } from "./SlideCompareModal";
import type { DeckStart } from "./useChat";
import { deckJobHasNews, editOrigin, JOB_WARNINGS } from "./feed";

/**
 * Шаги работы говорят фразами, а не показывают карточки.
 *
 * Раньше каждый шаг был рамкой с заголовком, значком состояния и сеткой полей: шесть таких
 * рамок подряд читались как приборная панель, а не как разговор. Теперь шаблон, материалы,
 * задача, генерация, аудит и правки — это реплики одного собеседника: короткая фраза, при
 * необходимости кнопки-ответы, метка шага внизу. Рамка осталась там, где есть что показать:
 * миниатюры «до/после» и раскрытые подробности задания.
 */

type Msg<K extends string> = Extract<ChatMessage, { kind: K }>;

export interface CardContext {
  project: Project;
  session: GenerationSession;
  onResolveTemplate: (messageId: string, fileId: string, answer: PptxAnswer) => void;
  onEditBrief: () => void;
  onSetPurpose: (purpose: BriefDraft["purpose"]) => void;
  onGenerate: () => void;
  generating: boolean;
  onRepairAll?: () => void;
  onRetryImport: () => void;
  /** Задание «Открыть как презентацию»: ход сборки — строкой под «Открываю…», карточки сборки нет. */
  deckJob: boolean;
  /** Открытие готовой презентации, начатое в этой вкладке (таймер идёт с ответа, а не с задания). */
  deckStart?: DeckStart | null;
  /** Когда слайды готовой презентации стали видны в редакторе (без редактора — файл опубликован). */
  deckShownAt?: string | null;
}

/** Реплика ассистента: обычный текст ленты. */
function Say({ children, testId }: { children: React.ReactNode; testId?: string }) {
  return <Text size="sm" className="chat-assistant-text" data-testid={testId}>{children}</Text>;
}

/** Идёт работа: строка с бегущим многоточием. Точку в конце не ставим — её дорисует анимация. */
function Doing({ children, testId }: { children: React.ReactNode; testId?: string }) {
  return <Text size="sm" className="chat-hint" data-testid={testId}>{children}</Text>;
}

/** Пояснение под репликой: мелким серым, чтобы не спорить с самой фразой. */
function Aside({ children, testId }: { children: React.ReactNode; testId?: string }) {
  return <Text size="xs" c="dimmed" data-testid={testId}>{children}</Text>;
}

export interface Option {
  label: string;
  onClick: () => void;
  testId?: string;
}

/** Варианты ответа под сообщением: кнопки-реплики, как у ботов в мессенджерах. */
export function Options({ options }: { options: Option[] }) {
  return (
    <div className="chat-options">
      {options.map((o) => (
        <button key={o.label} type="button" className="chat-opt" onClick={o.onClick} data-testid={o.testId}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Служебное под репликой: пока не спросили, места не занимает. */
function Details({ label, children, testId }: { label: string; children: React.ReactNode; testId?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" className="chat-more" onClick={() => setOpen((o) => !o)} data-testid={testId}>
        {open ? "скрыть подробности" : label}
      </button>
      {open && children}
    </div>
  );
}

/** Вопрос о PPTX: шаблон оформления, готовая презентация или материал. */
export function PptxQuestion({ name, resolved, uploading, onAnswer, testId, progress }: { name: string; resolved?: PptxAnswer; uploading?: boolean; onAnswer: (answer: PptxAnswer) => void; testId: string; progress?: React.ReactNode }) {
  const text = resolved === "template"
    ? uploading ? `Разберу «${name}» как шаблон, как только файл загрузится.` : `«${name}» загружен, приступаю к разбору шаблона.`
    : resolved === "deck"
      ? uploading ? `Открою «${name}» как готовую презентацию, как только файл загрузится.` : `Открываю «${name}» как готовую презентацию: слайды остаются как есть, править можно из чата или в редакторе.`
      : resolved === "material"
        ? uploading ? `Считаю «${name}» материалом: импортирую, как только файл загрузится.` : `Считаю «${name}» материалом: текст слайдов пойдёт в содержание.`
        : `«${name}» похоже на презентацию. Что с ней сделать?`;
  return (
    <Stack gap={6} data-testid={testId}>
      <Say>{text}</Say>
      {progress}
      {!resolved && (
        <>
          <Options
            options={[
              { label: "Сделать шаблоном", onClick: () => onAnswer("template"), testId: "answer-template" },
              { label: "Открыть как презентацию", onClick: () => onAnswer("deck"), testId: "answer-deck" },
              { label: "Взять как материал", onClick: () => onAnswer("material"), testId: "answer-material" },
            ]}
          />
          <Aside>Шаблон — по нему собираются новые слайды; готовая презентация — слайды переносятся как есть, а править их можно из чата или в редакторе; материал — текст слайдов пойдёт в содержание.</Aside>
        </>
      )}
    </Stack>
  );
}

export function TemplateQuestionCard({ m, ctx }: { m: Msg<"template_question">; ctx: CardContext }) {
  const file = ctx.project.files.find((f) => f.file_id === m.file_id);
  const deck = m.resolved === "deck" ? deckState(m, ctx) : null;
  return (
    <PptxQuestion
      name={file?.name ?? "файл"}
      resolved={m.resolved}
      onAnswer={(answer) => ctx.onResolveTemplate(m.event_id, m.file_id, answer)}
      testId={`template-question-${m.event_id}`}
      progress={deck?.kind === "opening"
        ? <DeckProgress session={ctx.session} since={deck.since} />
        : deck?.kind === "opened" ? <DeckOpened ms={deck.ms} /> : null}
    />
  );
}

type DeckState = { kind: "opening"; since?: string } | { kind: "opened"; ms: number };

/**
 * Что показать под «Открываю…»: ход, пока слайдов ещё не видно, и итог «открыта за …», когда
 * они появились; фоновый разбор после этого строку не держит. Строка стоит под последним таким
 * ответом: повторная сборка старые сообщения не оживляет. Открытие, начатое в этой вкладке,
 * считается от ответа до появления слайдов в редакторе; после перезагрузки — по серверу, от
 * задания до публикации файла.
 */
function deckState(m: Msg<"template_question">, ctx: CardContext): DeckState | null {
  const { project, session, deckStart } = ctx;
  const mine = deckStart?.fileId === m.file_id ? deckStart : null;
  if (mine?.preparing) return { kind: "opening", since: mine.since };
  const last = [...project.events].reverse().find((e) => e.role === "assistant" && e.kind === "template_question" && e.resolved === "deck" && e.file_id === m.file_id);
  if (last?.event_id !== m.event_id || !ctx.deckJob || !project.job_id || session.jobId !== project.job_id) return null;
  const file = project.files.find((f) => f.file_id === m.file_id);
  if (file?.template_id !== project.template_id) return null;
  const result = session.result;
  const readyAt = session.variant?.ready_at;
  const span = (from: string, to: string) => Math.max(0, Date.parse(to) - Date.parse(from));
  if (mine) {
    const shownAt = ctx.deckShownAt ?? (session.terminal ? readyAt : undefined);
    if (shownAt) return { kind: "opened", ms: span(mine.since, shownAt) };
    return session.terminal ? null : { kind: "opening", since: mine.since };
  }
  if (!result) return null;
  if (readyAt) return { kind: "opened", ms: span(result.created_at, readyAt) };
  return session.terminal ? null : { kind: "opening" };
}

/** 83 секунды → «1:23»: таймер строкой, цифры одной ширины. */
function clock(ms: number | null): string {
  const s = Math.floor((ms ?? 0) / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Ход открытия готовой презентации под репликой: время, этап и процент одной строкой. */
function DeckProgress({ session, since }: { session: GenerationSession; since?: string }) {
  const result = session.result;
  const elapsed = useElapsed(since ?? result?.created_at ?? null);
  const raw = result?.progress?.percent;
  const current = typeof raw === "number" && Number.isFinite(raw) ? Math.max(0, Math.min(100, raw)) : null;
  // Процент не откатывается: после показа файла разбор в фоне начинает свою шкалу заново.
  const [top, setTop] = useState<number | null>(null);
  const rising = current != null && (top == null || current > top);
  if (rising) setTop(current);
  const percent = rising ? current : top;
  const shown = Boolean(session.variant?.ready_at);
  const message = shown ? "Открываю слайды в редакторе"
    : result?.progress?.message || (result ? STAGE_LABELS[result.stage] : "Загружаю презентацию");
  return (
    <div className="deck-progress" data-testid="deck-progress" aria-busy="true">
      <Group gap={6} wrap="nowrap">
        <IconClock size={14} stroke={1.8} className="deck-progress-time" aria-hidden />
        <Text size="xs" fw={600} className="deck-progress-time" data-testid="deck-progress-timer">{clock(elapsed)}</Text>
        <Text size="xs" c="dimmed" truncate style={{ flex: 1 }} role="status" aria-live="polite">{message}</Text>
        {percent != null && <Text size="xs" fw={600} data-testid="deck-progress-percent">{Math.round(percent)}%</Text>}
      </Group>
      {percent != null
        ? <Progress size={4} value={percent} aria-label="Открытие презентации" aria-valuenow={percent} />
        : <div className={progressStyles.track} style={{ height: 4 }} role="progressbar" aria-label="Открытие презентации"><div className={progressStyles.shimmer} /></div>}
    </div>
  );
}

/** Итог в истории чата: сколько открывалась презентация. */
function DeckOpened({ ms }: { ms: number }) {
  return (
    <Group gap={6} wrap="nowrap" data-testid="deck-opened">
      <IconCircleCheck size={14} stroke={1.8} color="var(--mantine-color-green-7)" aria-hidden />
      <Text size="xs" c="dimmed">Презентация открыта за {formatMs(Math.max(1000, ms))}</Text>
    </Group>
  );
}

export function TemplateCard({ m, ctx }: { m: Msg<"template_card">; ctx: CardContext }) {
  const detail = usePolling<TemplateDetail>(() => api.templates.get(m.template_id), (d) => d.status === "succeeded" || d.status === "failed", [m.template_id]);
  const profile = detail.data?.profile;
  const current = ctx.project.template_id === m.template_id;
  // Шаблон удалили из библиотеки: сообщение остаётся в истории, но профиля у него больше нет.
  const gone = detail.error instanceof ApiError && detail.error.status === 404;
  const timing = detail.data?.timing;
  const elapsed = useElapsed(timing?.started_at ?? timing?.created_at, profile ? (timing?.finished_at ?? null) : null);
  const name = detail.data?.name ?? m.template_id;

  if (gone) {
    return <Say testId="template-card">Шаблон «{name}» удалён из библиотеки. Загрузите PPTX снова или выберите другой вверху справа.</Say>;
  }
  if (detail.error && !detail.data) return <Say testId="template-card">{detail.error.message}</Say>;
  if (detail.data?.status === "failed") {
    return <Say testId="template-card">Не удалось разобрать шаблон «{name}»: {detail.data.error?.message ?? "ошибка анализа"}. Загрузите PPTX снова или выберите другой вверху справа.</Say>;
  }
  if (!profile) {
    // Что делается, сказано репликой выше («загружен, приступаю к разбору»): здесь только ход.
    return (
      <Stack gap={4} data-testid="template-card">
        <div className="deck-progress" aria-busy="true" data-testid="template-progress">
          <Group gap={6} wrap="nowrap">
            <IconClock size={14} stroke={1.8} className="deck-progress-time" aria-hidden />
            <Text size="xs" fw={600} className="deck-progress-time" data-testid="template-progress-timer">{clock(elapsed)}</Text>
            <Text size="xs" c="dimmed" truncate style={{ flex: 1 }} role="status">Разбор: образцы, палитра и шрифты</Text>
          </Group>
          <div className={progressStyles.track} style={{ height: 4 }} role="progressbar" aria-label="Разбор шаблона"><div className={progressStyles.shimmer} /></div>
        </div>
        <Aside>Ждать не нужно — можно добавлять материалы.</Aside>
      </Stack>
    );
  }

  const fonts = profile.design_tokens.typography.fonts.slice(0, 2).map((f) => f.family).join(", ");
  return (
    <Stack gap={6} data-testid="template-card">
      <Say testId="template-profile">
        Разобрал шаблон «{name}»: {profile.patterns.length} {plural(profile.patterns.length, "композиция", "композиции", "композиций")} на {profile.stats.slides} {plural(profile.stats.slides, "слайде", "слайдах", "слайдах")}{fonts ? `, шрифты ${fonts}` : ""}.
      </Say>
      {timing?.duration_ms != null && (
        <Group gap={6} wrap="nowrap" data-testid="template-duration">
          <IconClock size={14} stroke={1.8} className="deck-progress-time" aria-hidden />
          <Text size="xs" c="dimmed">Разобран за <Text span size="xs" fw={600} className="deck-progress-time">{formatMs(Math.max(1000, timing.duration_ms))}</Text></Text>
        </Group>
      )}
      <Group gap={4}>
        {profile.design_tokens.colors.palette.slice(0, 8).map((c) => (
          <Tooltip key={c.hex} label={`${c.hex} · ${c.role}`}><ColorSwatch color={c.hex} size={14} /></Tooltip>
        ))}
      </Group>
      <Aside>
        {current ? "Слайды соберу из этих композиций; сменить шаблон можно вверху справа." : "Сейчас собираю по другому шаблону."}{" "}
        <Anchor href={`/templates?id=${encodeURIComponent(m.template_id)}`} target="_blank" rel="noreferrer" size="xs" data-testid="template-open-library">Что извлечено из шаблона</Anchor>
      </Aside>
    </Stack>
  );
}

export function ContentCard({ m, ctx }: { m: Msg<"content_card">; ctx: CardContext }) {
  const detail = usePolling<ContentDetail>(() => api.content.get(m.package_id), (d) => d.status === "succeeded" || d.status === "failed", [m.package_id]);
  const pkg = detail.data?.package;
  // Импорт упал: сообщение говорит об этом, а не крутит «читаю» бесконечно.
  const failed = detail.data?.status === "failed" ? (detail.data.error?.message ?? "импорт не удался") : detail.error ? detail.error.message : null;
  const files = ctx.project.files.filter((f) => m.file_ids.includes(f.file_id));
  const current = ctx.project.package_id === m.package_id;
  const names = files.map((f) => `«${f.name}»`).join(", ");

  if (failed) {
    return (
      <Stack gap={6} data-testid="content-card">
        <Say testId="import-error">Не смог прочитать материалы: {failed.split("\n")[0]}</Say>
        {current && <Options options={[{ label: "Повторить импорт", onClick: ctx.onRetryImport, testId: "import-retry" }]} />}
      </Stack>
    );
  }
  if (!pkg) {
    return <Doing testId="content-card">{names ? `Читаю ${names}` : "Читаю материалы"}</Doing>;
  }
  return (
    <Stack gap={4} data-testid="content-card">
      <Say testId="import-summary">
        {names ? `Прочитал ${names}: ` : "Собрал содержание по брифу: "}
        {pkg.blocks.length} {plural(pkg.blocks.length, "блок", "блока", "блоков")}, {pkg.facts.length} {plural(pkg.facts.length, "факт", "факта", "фактов")}, {pkg.datasets.length} {plural(pkg.datasets.length, "таблица", "таблицы", "таблиц")}, {pkg.assets.length} {plural(pkg.assets.length, "изображение", "изображения", "изображений")}.
        {pkg.missing_data && pkg.missing_data.length > 0 ? ` Не хватает: ${pkg.missing_data.map((x) => x.what).join(", ")} — выдумывать не буду.` : ""}
      </Say>
      {!current && <Aside>Потом материалы переимпортировались — в дело идёт последний разбор.</Aside>}
    </Stack>
  );
}

export function BriefCard({ m, ctx }: { m: Msg<"brief_card">; ctx: CardContext }) {
  const { brief, settings } = ctx.project;
  const missingPurpose = !brief.purpose;
  const missing = [!ctx.project.template_id ? "шаблон" : null, !ctx.project.package_id ? "материалы или бриф" : null, missingPurpose ? "назначение" : null].filter(Boolean) as string[];
  const ready = missing.length === 0;
  const purpose = brief.purpose ? PURPOSE_LABELS[brief.purpose] ?? brief.purpose : "";

  // Понятое пересказывается фразой, а не таблицей полей: так видно, что именно услышано,
  // и сразу понятно, что поправить, если услышано не то.
  const facts: string[] = [];
  if (purpose) facts.push(brief.audience ? `${purpose} для ${brief.audience}` : purpose);
  else if (brief.audience) facts.push(`Аудитория — ${brief.audience}`);
  if (brief.title) facts.push(`тема — «${brief.title}»`);
  if (brief.goal) facts.push(`цель — ${brief.goal}`);
  const volume = settings.mode === "exact" ? `ровно ${settings.exact}` : `${settings.min}–${settings.max}`;
  const variants = settings.variants.length === 3 ? "трёх вариантах вёрстки" : `вариантах: ${settings.variants.map((v) => (VARIANT_LABELS[v] ?? v).toLowerCase()).join(", ")}`;

  return (
    <Stack gap={6} data-testid="brief-card">
      <Say>{m.understood.length ? "Понял задачу так." : "Задача."}</Say>
      {facts.length > 0 && <Say>{facts.join(", ")}.</Say>}
      {/* Число слайдов называем, только если о нём просили: диапазон по умолчанию — не обещание. */}
      <Say>Соберу {slideCountAsked(settings) ? `${volume} ${plural(settings.mode === "exact" ? settings.exact : settings.max, "слайд", "слайда", "слайдов")}` : "презентацию"} в {variants}{settings.contextual ? "" : ", без контекстного аудита"}.</Say>

      {missingPurpose && (
        <>
          <Say>Уточните назначение — от него зависит структура колоды:</Say>
          <Options options={PURPOSE_OPTIONS.map((o) => ({ label: o.label, onClick: () => ctx.onSetPurpose(o.value as BriefDraft["purpose"]), testId: `purpose-${o.value}` }))} />
        </>
      )}
      {m.understood.length > 0 && m.brief_source && (
        <Aside testId="brief-source">
          {m.brief_source === "model" ? "Поля из сообщения выделила модель; проверьте и поправьте при необходимости." : "Поля из сообщения выделены по правилам: модель была недоступна."}
        </Aside>
      )}

      <Group gap="sm" align="center">
        <Button size="sm" leftSection={<IconRocket size={15} />} disabled={!ready} loading={ctx.generating} onClick={ctx.onGenerate} data-testid="generate">
          {ctx.project.job_id ? "Сгенерировать заново" : "Сгенерировать"}
        </Button>
        <button type="button" className="chat-more" onClick={ctx.onEditBrief} data-testid="edit-brief">изменить задачу</button>
      </Group>
      <Aside>{ready ? "Три варианта строятся параллельно, слайды появятся справа." : `Не хватает: ${missing.join(", ")}.`}</Aside>
    </Stack>
  );
}

/** Сколько вариантов — словом: «в трёх вариантах» читается как речь, «в 3 вариантах» — как отчёт. */
const COUNT_WORDS = ["", "одном", "двух", "трёх", "четырёх", "пяти", "шести"];

/**
 * Что делает задание — фразой от первого лица. Названия этапов из `STAGE_LABELS` написаны для
 * таблицы метрик («Сборка PPTX», «Планы вариантов»): в ленте они звучат как строка журнала, а
 * не как ответ собеседника, поэтому у речи свои слова.
 */
const JOB_PHRASE: Record<string, string> = {
  queued: "Ставлю задание в очередь",
  analyze: "Разбираю шаблон",
  import: "Читаю материалы",
  story: "Продумываю структуру",
  plan: "Раскладываю содержание по слайдам",
  compose: "Собираю слайды",
  export: "Сохраняю файлы",
  audit: "Проверяю слайды",
  repair: "Исправляю находки",
  finalize: "Заканчиваю",
  done: "Заканчиваю",
};

/** То же для строки варианта: «Сбалансированный — сборка», а не «сборка pptx». */
const VARIANT_STAGE: Record<string, string> = {
  queued: "в очереди",
  analyze: "разбор шаблона",
  import: "чтение материалов",
  story: "структура",
  plan: "план слайдов",
  compose: "сборка",
  export: "экспорт",
  audit: "проверка",
  repair: "исправления",
  finalize: "завершение",
  done: "готов",
};

function variantLine(v: GenerationResult["variants"][number]): string {
  const label = VARIANT_LABELS[v.variant_id] ?? v.variant_id;
  if (v.status === "failed") return `${label} — не собрался${v.error?.message ? `: ${v.error.message.toLowerCase()}` : ""}`;
  if (v.status === "ready" || v.status === "needs_review") {
    return `${label} — готов${v.slide_count ? `, ${v.slide_count} ${plural(v.slide_count, "слайд", "слайда", "слайдов")}` : ""}`;
  }
  const stage = (v.stages ?? []).find((s) => s.status === "running")?.stage;
  if (v.status === "running") return `${label} — ${(stage && VARIANT_STAGE[stage]) || "собирается"}`;
  return `${label} — в очереди`;
}

/**
 * Генерация в ленте: пока идёт — строка о том, что происходит сейчас, и по строке на вариант;
 * когда закончилась — одна фраза с итогом. Этапы, метрики, идентификатор задания и режим
 * исполнения ушли под «подробности»: при чтении они не нужны, при разборе — открываются.
 */
export function JobCard({ m, ctx }: { m: Msg<"job_card">; ctx: CardContext }) {
  const { session } = ctx;
  const result = session.jobId === m.job_id ? session.result : null;
  const terminal = result ? TERMINAL_STATES.has(result.status) : false;
  const elapsed = useElapsed(result?.created_at, terminal ? (result?.finished_at ?? result?.created_at) : null);

  if (session.jobId !== m.job_id) return <Say>Это задание заменено новым — ход новой сборки ниже.</Say>;
  if (!result) {
    return <Doing testId="job-card">{session.job.error ? session.job.error.message : "Ставлю задание в очередь"}</Doing>;
  }

  const total = terminal ? (result.metrics.totals?.duration_ms ?? elapsed) : elapsed;
  const done = result.variants.filter((v) => v.status === "ready" || v.status === "needs_review");
  const broken = result.variants.filter((v) => v.status === "failed");
  const counts = [...new Set(done.map((v) => v.slide_count).filter((n): n is number => typeof n === "number"))];
  const slides = counts.length === 1
    ? `${counts[0]} ${plural(counts[0], "слайд", "слайда", "слайдов")}`
    : counts.length > 1
      ? `${Math.min(...counts)}–${Math.max(...counts)} слайдов`
      : "слайды";
  const failMessage = (result.error?.message ?? "задание завершилось ошибкой").replace(/\.\s*$/, "");
  const warnings = result.warnings?.filter((w) => JOB_WARNINGS.has(w.code) && w.code !== "slide_count_short") ?? [];
  // Содержания меньше, чем просили: варианты собраны короче, и это говорится обычной фразой.
  const short = result.warnings?.find((w) => w.code === "slide_count_short");

  // Готовая презентация: в ленте уже сказано «открываю как есть», прогресс — над слайдами.
  // Лента показывает сборку, только если открыть не вышло или в слайдах что-то изменилось.
  if (ctx.deckJob) {
    if (!deckJobHasNews(result)) return null;
    return (
      <Stack gap={6} data-testid="job-card" data-state={result.status}>
        {result.status === "failed" && (session.variant?.ready_at
          ? <Say testId="job-summary">Слайды открыты, но разбор презентации не завершился: {failMessage}. Править можно в редакторе.</Say>
          : <Say testId="job-summary">Не открыл презентацию: {failMessage}.{result.error?.retryable ? " Можно повторить." : ""}</Say>)}
        {warnings.map((w) => <Say key={w.code} testId={w.code === "chart_images" ? "job-charts" : "job-warning"}>{w.message}</Say>)}
      </Stack>
    );
  }

  const summary = !terminal ? (
    <Doing testId="job-summary">{JOB_PHRASE[result.stage] ?? STAGE_LABELS[result.stage]}</Doing>
  ) : result.status === "canceled" ? (
    <Say testId="job-summary">Отменил сборку{total ? ` через ${formatMs(total)}` : ""}.</Say>
  ) : done.length === 0 ? (
    <Say testId="job-summary">Не собрал: {failMessage}.{result.error?.retryable ? " Можно повторить." : ""}</Say>
  ) : (
    <Say testId="job-summary">
      Собрал {slides}{done.length > 1 ? ` в ${COUNT_WORDS[done.length] ?? done.length} ${plural(done.length, "варианте", "вариантах", "вариантах")}` : ""} за {formatMs(total)}.
    </Say>
  );

  return (
    <Stack gap={6} data-testid="job-card" data-state={result.status}>
      {summary}
      {terminal && short && done.length > 0 && <Say testId="job-short">{short.message}</Say>}
      {terminal && broken.length > 0 && done.length > 0 && (
        <Say testId="job-partial">
          {broken.length === 1
            ? `Вариант «${VARIANT_LABELS[broken[0].variant_id] ?? broken[0].variant_id}» не собрался`
            : `${broken.length} ${plural(broken.length, "вариант", "варианта", "вариантов")} не собрались`}
          {" "}— остальные готовы, повторить сборку можно кнопкой «Повторить» в шапке проекта.
        </Say>
      )}
      <Stack gap={2}>
        {result.variants.map((v) => (
          <Text key={v.variant_id} size="sm" className="job-variant-line" data-status={v.status} data-testid={`variant-progress-${v.variant_id}`}>
            {variantLine(v)}
          </Text>
        ))}
      </Stack>
      {warnings.map((w) => (
        <Aside key={w.code} testId={w.code === "chart_images" ? "job-charts" : "job-warning"}>{w.message}</Aside>
      ))}
      <Details label="подробности" testId="job-details"><JobDetails result={result} /></Details>
    </Stack>
  );
}

const FIRST_SHOWN = 5;

/**
 * Аудит разговаривает, а не показывает приборы: одна фраза о проверке, список замечаний
 * строками и одно предложение исправить. Нажатие на строку ведёт к месту на слайде.
 *
 * Здесь сознательно нет шкал, галочек и счётчиков по категориям: это сообщение ассистента,
 * а не панель управления. Подробности (какая проверка, чем чинится) видны на слайде и в
 * отчёте `audit.json`; в ленте важно, что не так и что с этим делать.
 */
export function AuditCard({ m, ctx }: { m: Msg<"audit_card">; ctx: CardContext }) {
  const { session } = ctx;
  const [expanded, setExpanded] = useState(false);
  if (session.jobId !== m.job_id || !session.result) return null;
  const variant = session.variant;
  const audit = variant?.audit;
  const busy = session.busy || Boolean(session.repairJob) || Boolean(session.editJob);
  const stale = session.viewRevision !== session.currentRevision;

  if (!audit || audit.status === "pending" || audit.status === "running") {
    return <Doing testId="audit-summary">Проверяю слайды</Doing>;
  }

  const report = session.audit.data;
  const issues = report?.issues ?? [];
  const serious = issues.filter((i) => i.severity === "error" || i.severity === "blocking").length;
  const fixable = issues.filter((i) => i.fix.available);
  const shown = expanded ? issues : issues.slice(0, FIRST_SHOWN);
  const slides = report?.deck.slide_count;
  const before = session.prevAudit.data?.summary.issues_total;
  const repaired = session.viewRevision > 1 && typeof before === "number";
  const label = VARIANT_LABELS[variant?.variant_id ?? ""] ?? variant?.variant_id ?? "";
  const others = session.result.variants.filter(
    (v) => v.variant_id !== variant?.variant_id && (v.audit?.issues_total ?? 0) > 0,
  );

  return (
    <Stack gap={6} data-testid="audit-card">
      <Say testId="audit-summary">
        {issues.length === 0
          ? `Проверил ${slides ?? ""} слайдов — всё в порядке.`
          : `Проверил ${slides ?? ""} слайдов и нашёл ${issues.length} ${plural(issues.length, "замечание", "замечания", "замечаний")}${serious ? `, из них ${serious} ${plural(serious, "серьёзное", "серьёзных", "серьёзных")}` : ""}.`}
        {report?.coverage.complete === false ? " Часть проверок выполнить не удалось." : ""}
      </Say>

      {repaired && <Say testId="audit-repaired">После исправления стало {issues.length} вместо {before}.</Say>}

      {shown.length > 0 && (
        <Stack gap={2}>
          {shown.map((issue) => (
            <Text
              key={issue.issue_id}
              size="sm"
              className="audit-line"
              data-active={session.activeIssue === issue.issue_id || undefined}
              onClick={() => session.focusIssue(issue)}
              data-testid={`issue-${issue.issue_id}`}
            >
              Слайд {issue.slide_index + 1} — {issue.message.toLowerCase()}
            </Text>
          ))}
        </Stack>
      )}

      {issues.length > FIRST_SHOWN && !expanded && (
        <Anchor size="sm" onClick={() => setExpanded(true)} data-testid="audit-more">
          показать ещё {issues.length - FIRST_SHOWN}
        </Anchor>
      )}

      {fixable.length > 0 && (
        <Group gap="xs" align="center">
          <Say>
            {fixable.length === issues.length ? "Могу исправить всё это сам." : `Из них ${fixable.length} могу исправить сам.`}
          </Say>
          <Button size="xs" disabled={busy || stale} loading={busy} onClick={ctx.onRepairAll} data-testid="repair-all">
            Исправить
          </Button>
        </Group>
      )}

      {/* Проверены все варианты, а в ленте виден отчёт того, что показан справа. Молчать о
          других — значит показать «всё в порядке» там, где в соседнем варианте семь замечаний.
          Поэтому ассистент сам говорит, где они есть, и предлагает туда перейти. */}
      {others.length > 0 && (
        <>
          <Say testId="audit-others">
            {issues.length === 0
              ? `Это «${label}». Замечания есть в других вариантах:`
              : "В других вариантах тоже есть замечания:"}
          </Say>
          <Options
            options={others.map((v) => ({
              label: `${VARIANT_LABELS[v.variant_id] ?? v.variant_id} — ${v.audit?.issues_total} ${plural(v.audit?.issues_total ?? 0, "замечание", "замечания", "замечаний")}`,
              onClick: () => session.setSelectedVariant(v.variant_id),
              testId: `audit-other-${v.variant_id}`,
            }))}
          />
        </>
      )}
    </Stack>
  );
}

/**
 * Правка слайда по запросу: пока идёт — строка о ходе, потом фраза о сделанном и миниатюры
 * «до/после». Отказ — с причиной. Ход правки и ход исправления находок читаются одинаково:
 * это одно задание ревизии, просто в результате они лежат в разных списках.
 */
export function EditCard({ m, ctx }: { m: Msg<"edit_card">; ctx: CardContext }) {
  const { session } = ctx;
  // Крупный просмотр «до/после»: миниатюры в ленте малы, разницу на них не разглядеть.
  const [compare, setCompare] = useState<"before" | "after" | null>(null);
  const repairEntry = session.result?.repairs?.find((r) => r.repair_job_id === m.edit_job_id);
  const entry =
    session.result?.edits?.find((e) => e.edit_job_id === m.edit_job_id) ??
    (repairEntry
      ? {
          edit_job_id: repairEntry.repair_job_id,
          variant_id: repairEntry.variant_id,
          base_revision: repairEntry.base_revision ?? 0,
          new_revision: repairEntry.new_revision,
          result: repairEntry.result === "applied" ? ("applied" as const) : ("failed" as const),
          message: repairEntry.message,
          changed_slide_ids: repairEntry.changed_slide_ids,
          origin: "audit" as const,
          change_note: repairEntry.message,
          summary: undefined,
        }
      : undefined);
  const settled = Boolean(entry && entry.result !== undefined);
  const status = usePolling<JobStatus>(!settled ? () => api.jobs.get(m.edit_job_id) : null, (s) => TERMINAL_STATES.has(s.status), [m.edit_job_id, settled]);
  const manual = entry?.origin === "editor" || status.data?.kind === "slide_patch" || editOrigin(m.edit_job_id) === "editor";
  const changedCount = entry?.changed_slide_ids?.length ?? 0;
  const title = manual ? (changedCount > 1 ? `Правки на слайдах` : `Правки на слайде ${m.slide_index + 1}`) : `Правка слайда ${m.slide_index + 1}`;
  const variantLabel = VARIANT_LABELS[m.variant_id] ?? m.variant_id;
  if (session.jobId !== m.job_id) return <Say>Задание заменено — эта правка относилась к прошлой сборке.</Say>;
  const failed = status.data?.status === "failed" || entry?.result === "failed";
  if (failed) {
    const message = entry?.message ?? status.data?.error?.message ?? "Правка не выполнена";
    return (
      <Stack gap={2} data-testid="edit-card">
        <Say>Не получилось: {message.toLowerCase()}</Say>
        <Aside>Выберите слайд слева и попросите другими словами.</Aside>
      </Stack>
    );
  }
  if (entry?.result === "unchanged" || (status.data?.status === "succeeded" && status.data.result?.unchanged)) {
    const reason = entry?.change_note ?? status.data?.result?.change_note ?? "";
    return (
      <Stack gap={2} data-testid="edit-card">
        <Say testId="edit-reason">Оставил слайд {m.slide_index + 1} как есть: {reason || "просьбу выполнить нельзя"}</Say>
        <Aside>Данные не выдумываю: добавьте материалы или уточните просьбу.</Aside>
      </Stack>
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
    const before = { label: `до · r${entry.base_revision}`, src: api.generations.artifactUrl(m.job_id, name(entry.base_revision)) };
    const after = { label: `после · r${entry.new_revision}`, src: api.generations.artifactUrl(m.job_id, name(entry.new_revision)) };
    return (
      <Stack gap={8} data-testid="edit-card">
        <Say testId="edit-note">
          {`${((manual ? entry.summary : undefined) || entry.change_note || (manual ? "Применил правки" : `Переделал слайд ${m.slide_index + 1}`)).replace(/[.;\s]+$/, "")} — ревизия ${entry.new_revision}.`}
        </Say>
        <SimpleGrid cols={2} spacing="xs">
          {([["before", before], ["after", after]] as const).map(([key, side]) => (
            <button key={key} type="button" className="compare-thumb" onClick={() => setCompare(key)} aria-label={`Открыть крупно: ${side.label}`} data-testid={`edit-compare-${key}`}>
              <SlideImage src={side.src} alt={side.label} />
              <Text size="xs" ta="center" c="dimmed">{side.label}</Text>
            </button>
          ))}
        </SimpleGrid>
        <SlideCompareModal
          opened={compare !== null}
          onClose={() => setCompare(null)}
          title={`${title} · ${variantLabel}`}
          before={before}
          after={after}
          initial={compare ?? "after"}
          onShow={show}
        />
        <Options options={[{ label: "Показать слайд", onClick: show, testId: "edit-show" }]} />
      </Stack>
    );
  }
  const message = status.data?.progress?.message ?? (manual ? "Применяю правки" : `Переделываю слайд ${m.slide_index + 1}`);
  return <Doing testId="edit-card">{status.error ? status.error.message : message}</Doing>;
}
