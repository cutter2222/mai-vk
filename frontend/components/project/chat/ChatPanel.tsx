"use client";

import { ActionIcon, Badge, Button, CloseButton, FileButton, Group, Stack, Text, Textarea, Tooltip } from "@mantine/core";
import { Dropzone } from "@mantine/dropzone";
import { notifications } from "@mantine/notifications";
import { IconArrowUp, IconFile, IconMicrophone, IconPaperclip, IconPlayerStopFilled, IconSlideshow } from "@tabler/icons-react";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { dictationSupported, useDictation } from "@/lib/hooks/useDictation";

import { formatBytes, VARIANT_LABELS } from "@/lib/format";
import type { OfficeSelection } from "@/lib/api/client";
import type { SlideTarget } from "@/lib/hooks/useGenerationSession";
import type { ChatMessage, PptxAnswer } from "@/lib/state/projects";

import { BriefCard, ContentCard, EditCard, JobCard, PptxQuestion, TemplateCard, TemplateQuestionCard, type CardContext } from "./cards";
import { deckJobHasNews, isVisibleProjectMessage } from "./feed";
import type { StagedPptx } from "./useChat";
import { MessageTime } from "./MessageTime";
import { useAssistantTyping } from "./useAssistantTyping";

/** Адресат сообщения из живого редактора: выбранный слайд или выделенный на нём объект. */
export interface LiveChatTarget {
  kind: "slide" | "object";
  slide: number;
  /** Вариант для слайда, текст или вид для объекта. */
  label: string;
}

interface Props {
  /** Голосовой ввод доступен (сервис распознавания отвечает, модель на месте). */
  speech?: boolean;
  officeSelection?: OfficeSelection | null;
  onDismissOfficeSelection?: () => void;
  liveTarget?: LiveChatTarget | null;
  onDismissLiveTarget?: () => void;
  suggestions?: string[];
  ctx: CardContext;
  onSend: (text: string, files: File[], target: SlideTarget | null) => Promise<void>;
  /** Разбирает брошенные файлы: презентации забирает сразу, остальные возвращает как вложения к сообщению. */
  onAttach: (files: File[]) => File[];
  staged: StagedPptx[];
  onAnswerStaged: (localId: string, answer: PptxAnswer) => void;
}

/** Наличие микрофона не меняется за жизнь страницы: подписываться не на что. */
const noSubscription = () => () => {};

/** Первое сообщение ленты: шаблон выбирается в правом углу шапки, в чате — задача и материалы. */
export const CHAT_GREETING = "Опишите, какая нужна презентация, или перетащите сюда материалы. Шаблон оформления выберите вверху справа.";

/**
 * Чат проекта: лента сообщений и карточек шагов, внизу поле ввода с вложениями; файлы можно бросать в любое место панели.
 * PPTX не ждёт отправки: вопрос «шаблон, готовая презентация или материал» появляется в ленте в момент броска, пока файл грузится.
 */
export function ChatPanel({ ctx, onSend, suggestions = [], onAttach, staged, onAnswerStaged, officeSelection, onDismissOfficeSelection, liveTarget, onDismissLiveTarget, speech = false }: Props) {
  const { project, session } = ctx;
  // Выбранный справа слайд — адресат сообщения: чип над полем ввода, крестик снимает адресацию.
  const target = session.slideTarget;
  const [text, setText] = useState("");
  const [pending, setPending] = useState<File[]>([]);
  // Что уходит: на текст ассистент отвечает, а одни вложения молча ложатся в «Файлы».
  const [sending, setSending] = useState<false | "text" | "files">(false);
  const listRef = useRef<HTMLDivElement | null>(null);
  const resetRef = useRef<() => void>(null);
  const followRef = useRef(true);
  // Приветствие — виртуальное первое сообщение; серверную историю не меняем.
  const greeting: ChatMessage = { event_id: "greeting", at: project.created_at, role: "assistant", kind: "text", text: CHAT_GREETING };
  const messages = [greeting, ...project.events];
  const typing = useAssistantTyping(project.project_id, messages, project.events.length || project.template_id ? messages.length : 0);
  const presentedGreeting = typing.present(greeting, 0);

  const addFiles = (files: File[]) => {
    const rest = onAttach(files);
    if (rest.length) setPending((p) => [...p, ...rest]);
  };

  // Сборка готовой презентации идёт полосой над слайдами; в ленте она появляется, только если есть что сказать.
  const quietDeckJob = ctx.deckJob && !deckJobHasNews(session.result);
  const visible = (m: ChatMessage) => isVisibleProjectMessage(m, session.jobId) && !(quietDeckJob && m.kind === "job_card");
  const events = project.events.filter(visible);
  // Лента прокручивается вниз при новом сообщении и когда сообщение о правке получает результат.
  const count = events.length + staged.length;
  const shown = project.events.flatMap((m, eventIndex) => {
    const index = eventIndex + 1;
    if (!visible(m)) return [];
    const presented = typing.present(m, index);
    return presented ? [{ message: presented, index }] : [];
  });
  const settledEdits = session.result?.edits?.length ?? 0;
  useEffect(() => {
    const el = listRef.current;
    if (el && followRef.current) el.scrollTop = el.scrollHeight;
  }, [count, session.result?.status, settledEdits, typing.progress, typing.activeIndex]);

  const blocked = session.editorDirty;
  // Надиктованная фраза дописывается в конец поля через пробел: дальше это обычный текст.
  const appendDictated = useCallback((phrase: string) => {
    setText((current) => joinText(current, phrase));
  }, []);
  const dictationError = useCallback((message: string) => {
    notifications.show({ color: "red", title: "Голосовой ввод", message });
  }, []);
  // Громкость голоса — кольцо вокруг кнопки: видно, что микрофон слышит, ещё до первых слов.
  const micRef = useRef<HTMLButtonElement | null>(null);
  const showLevel = useCallback((level: number) => {
    micRef.current?.style.setProperty("--mic-level", level.toFixed(2));
  }, []);
  const dictation = useDictation({ onText: appendDictated, onError: dictationError, onLevel: showLevel });
  // Пока фраза недоговорена, поле показывает её черновик после набранного; итог его заменит.
  // Черновик — не текст поля, поэтому правка поля ждёт итога (обычно меньше секунды).
  const shownText = dictation.interim ? joinText(text, dictation.interim) : text;
  // Кнопка — только если сервис распознавания есть и браузер даёт микрофон (HTTPS или localhost).
  const micAvailable = useSyncExternalStore(noSubscription, dictationSupported, () => false);
  const showMic = speech && micAvailable;
  const toggleMic = () => {
    if (dictation.recording) void dictation.stop();
    else if (dictation.state === "idle") void dictation.start();
  };
  useEffect(() => {
    if (!dictation.recording) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") void dictation.stop(); };
    // На погружении: открытая подсказка над кнопкой сама забирает Esc и дальше его не пускает.
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [dictation]);
  const dispatch = async (t: string, f: File[], to: SlideTarget | null) => {
    setSending(t.trim() ? "text" : "files");
    try {
      await onSend(t, f, to);
    } finally {
      setSending(false);
    }
  };
  const submit = async () => {
    if (sending || blocked || (!shownText.trim() && pending.length === 0)) return;
    // Отправка выключает запись: уходит то, что видно в поле (с черновиком недоговорённой
    // фразы), недослушанное не дописывается.
    if (dictation.state !== "idle") dictation.cancel();
    const t = shownText;
    const f = pending;
    const to = t.trim() ? target : null;
    setText("");
    setPending([]);
    resetRef.current?.();
    await dispatch(t, f, to);
  };
  // Подсказка — готовый ответ: нажатие сразу его отправляет. Набранный текст и вложения
  // остаются в поле ввода.
  const choose = (option: string) => {
    if (sending || blocked) return;
    void dispatch(option, [], null);
  };
  const placeholder = dictation.recording ? "Говорите — текст появится здесь…" : liveTarget ? (liveTarget.kind === "slide" ? `Что изменить на слайде ${liveTarget.slide}?` : "Что изменить в выделенном объекте?") : officeSelection ? (officeSelection.objects.length > 1 ? "Что изменить в выбранных объектах или куда их переместить?" : "Что изменить в объекте или куда его переместить?") : target
    ? `Что изменить на слайде ${target.slideIndex + 1}?`
    : count === 0
      ? "Опишите задачу или перетащите файлы…"
      : "Напишите, что изменить, или добавьте файлы…";

  return (
    // Зона перетаскивания Mantine выключает выделение текста (user-select: none) у себя и у
    // вложенного — без возврата сообщения ленты нельзя было выделить и скопировать.
    <Dropzone
      onDrop={addFiles}
      activateOnClick={false}
      styles={{ root: { border: 0, padding: 0, background: "transparent", borderRadius: 0, display: "flex", flexDirection: "column", flex: 1, minHeight: 0, userSelect: "text" }, inner: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0, pointerEvents: "auto", userSelect: "text" } }}
      data-testid="chat-dropzone"
    >
      <div className="chat-list" ref={listRef} data-testid="chat-list" onScroll={(e) => {
        const el = e.currentTarget;
        followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 64;
      }}>
        <div className="chat-msg chat-msg-assistant" data-testid="msg-assistant">
          <Stack gap="xs">
            <Text size="sm" className="chat-assistant-text" data-testid="chat-greeting" aria-busy={typing.activeIndex === 0} data-typing={typing.activeIndex === 0 || undefined}>
              {presentedGreeting?.kind === "text" ? presentedGreeting.text : ""}
            </Text>
          </Stack>
          <MessageTime at={project.created_at} />
        </div>
        {shown.map(({ message: m, index }) => (
          <div key={index} className={`chat-msg chat-msg-${m.role}`} data-testid={`msg-${m.role}`} data-typing={index === typing.activeIndex || undefined} aria-busy={index === typing.activeIndex || undefined}>
            {renderMessage(m, ctx)}
            <MessageTime at={m.at} />
          </div>
        ))}
        {/* Сам бросок PPTX в ленту не пишется: вопрос называет файл. */}
        {staged.map((s) => (
          <div key={s.local_id} className="chat-msg chat-msg-assistant" data-testid="msg-assistant">
            <PptxQuestion name={s.name} resolved={s.answer} uploading onAnswer={(answer) => onAnswerStaged(s.local_id, answer)} testId={`template-question-${s.local_id}`} />
          </div>
        ))}
      </div>
      <div className="chat-composer">
        {officeSelection && <Group gap={6} mb={8} data-testid="office-object-target">
          <Text size="xs">Слайд {officeSelection.slide} · {officeSelection.label} · v{officeSelection.revision}</Text>
          <CloseButton size="sm" aria-label="Снять выбор объекта" onClick={onDismissOfficeSelection} />
          <Text size="xs" c="dimmed">{officeSelection.objects.length > 1 ? "Правка только выбранных объектов. Например: «перемести все три правее»." : "Правка только этого объекта. Например: «сократи текст» или «перенеси правее»."}</Text>
        </Group>}
        {liveTarget && (
          <Group gap={6} mb={8} data-testid="live-target" data-kind={liveTarget.kind}>
            <Badge
              color="ink"
              variant="light"
              size="sm"
              leftSection={<IconSlideshow size={11} />}
              rightSection={<CloseButton size={12} aria-label="Не относить к слайду" onClick={onDismissLiveTarget} data-testid="live-target-dismiss" />}
            >
              Слайд {liveTarget.slide} · {liveTarget.label}
            </Badge>
          </Group>
        )}
        {suggestions.length > 0 && <Group gap={6} mb="xs" data-testid="chat-suggestions">
          {suggestions.map((option) => <Button key={option} size="compact-xs" variant="light" disabled={Boolean(sending) || blocked} onClick={() => choose(option)}>{option}</Button>)}
        </Group>}
        {target && (
          <Group gap={6} mb={8} data-testid="slide-target">
            <Badge
              color="ink"
              variant="light"
              size="sm"
              leftSection={<IconSlideshow size={11} />}
              rightSection={<CloseButton size={12} aria-label="Не относить к слайду" onClick={session.dismissTarget} data-testid="slide-target-dismiss" />}
            >
              Слайд {target.slideIndex + 1} · {VARIANT_LABELS[target.variantId] ?? target.variantId} · r{target.revision}
            </Badge>
            <Text size="xs" c="dimmed">правка создаст новую ревизию варианта</Text>
          </Group>
        )}
        {pending.length > 0 && (
          <Group gap={6} mb={8} data-testid="pending-files">
            {pending.map((f, i) => (
              <Badge key={`${f.name}-${i}`} color="gray" size="sm" leftSection={<IconFile size={11} />} rightSection={<CloseButton size={12} aria-label="Убрать файл" onClick={() => setPending((p) => p.filter((_, j) => j !== i))} />}>
                {f.name} · {formatBytes(f.size)}
              </Badge>
            ))}
          </Group>
        )}
        <div className="chat-input">
          <FileButton resetRef={resetRef} multiple onChange={addFiles} accept=".pptx,.docx,.xlsx,.csv,.pdf,.md,.txt,.png,.jpg,.jpeg,.mp4,.mov">
            {(props) => (
              <Tooltip label="Прикрепить шаблон или материалы">
                <ActionIcon {...props} variant="subtle" color="gray" size="lg" aria-label="Прикрепить файлы" data-testid="chat-attach"><IconPaperclip size={18} /></ActionIcon>
              </Tooltip>
            )}
          </FileButton>
          <Textarea
            variant="unstyled"
            placeholder={placeholder}
            autosize
            // Две строки сразу: высота поля не прыгает, когда подсказка в нём короче или длиннее.
            minRows={2}
            maxRows={6}
            value={shownText}
            readOnly={Boolean(dictation.interim)}
            onChange={(e) => setText(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit();
              }
            }}
            style={{ flex: 1 }}
            data-testid="chat-input"
          />
          {(dictation.pending > 0 || dictation.speaking) && <Text size="sm" c="dimmed" className="chat-mic-pending" aria-label={dictation.speaking ? "Слушаю" : "Распознаю фразу"} data-testid="chat-mic-pending">…</Text>}
          {showMic && (
            <Tooltip label={dictation.recording ? "Остановить запись" : "Надиктовать (русский)"}>
              <ActionIcon
                ref={micRef}
                variant={dictation.recording ? "filled" : "subtle"}
                color={dictation.recording ? "red" : "gray"}
                size="lg"
                className="chat-mic"
                data-recording={dictation.recording || undefined}
                onClick={toggleMic}
                loading={dictation.state === "starting" || dictation.state === "stopping"}
                disabled={blocked}
                aria-pressed={dictation.recording}
                aria-label={dictation.recording ? "Остановить запись" : "Надиктовать (русский)"}
                data-testid="chat-mic"
              >
                {dictation.recording ? <IconPlayerStopFilled size={16} /> : <IconMicrophone size={18} />}
              </ActionIcon>
            </Tooltip>
          )}
          <ActionIcon variant="filled" size="lg" onClick={submit} loading={Boolean(sending)} disabled={blocked || (!shownText.trim() && pending.length === 0)} aria-label="Отправить" data-testid="chat-send"><IconArrowUp size={18} /></ActionIcon>
        </div>
        {blocked && <Text size="xs" c="orange" mt={6} data-testid="chat-draft-hint">Сначала примените или отмените правки на слайде: черновик редактора ждёт решения.</Text>}
      </div>
    </Dropzone>
  );
}

/** Надиктованное дописывается через пробел. */
function joinText(current: string, phrase: string): string {
  return current.trim() ? `${current.replace(/\s+$/, "")} ${phrase}` : phrase;
}

function renderMessage(m: ChatMessage, ctx: CardContext) {
  if (m.role === "user") {
    const files = ctx.project.files.filter((f) => m.file_ids.includes(f.file_id));
    return (
      <Stack gap={6} align="flex-end">
        {files.length > 0 && (
          <Group gap={6} justify="flex-end">
            {files.map((f) => <Badge key={f.file_id} color="gray" size="sm" leftSection={<IconFile size={11} />}>{f.name}</Badge>)}
          </Group>
        )}
        {m.slide_ref && (
          <Badge color="ink" variant="light" size="sm" leftSection={<IconSlideshow size={11} />} data-testid="msg-slide-ref">
            к слайду {m.slide_ref.slide_index + 1} · {VARIANT_LABELS[m.slide_ref.variant_id] ?? m.slide_ref.variant_id}
          </Badge>
        )}
        {m.text && <div className="chat-bubble">{m.text}</div>}
      </Stack>
    );
  }
  switch (m.kind) {
    case "text":
      return <Text size="sm" className="chat-assistant-text">{m.text}</Text>;
    case "template_question":
      return <TemplateQuestionCard m={m} ctx={ctx} />;
    case "template_card":
      return <TemplateCard m={m} ctx={ctx} />;
    case "content_card":
      return <ContentCard m={m} ctx={ctx} />;
    case "brief_card":
      return <BriefCard m={m} ctx={ctx} />;
    case "job_card":
      return <JobCard m={m} ctx={ctx} />;
    case "edit_card":
      return <EditCard m={m} ctx={ctx} />;
    default:
      return null;
  }
}
