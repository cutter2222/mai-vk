"use client";

import { ActionIcon, Badge, Button, CloseButton, FileButton, Group, Loader, Stack, Text, Textarea, Tooltip } from "@mantine/core";
import { Dropzone } from "@mantine/dropzone";
import { IconArrowUp, IconFile, IconPaperclip, IconSlideshow } from "@tabler/icons-react";
import { Fragment, useEffect, useRef, useState } from "react";

import { formatBytes, VARIANT_LABELS } from "@/lib/format";
import type { OfficeSelection } from "@/lib/api/client";
import type { SlideTarget } from "@/lib/hooks/useGenerationSession";
import type { ChatMessage, PptxAnswer } from "@/lib/state/projects";

import { BriefCard, ContentCard, EditCard, JobCard, PptxQuestion, TemplateCard, TemplateQuestionCard, type CardContext } from "./cards";
import { isVisibleProjectMessage, TAG_HASH, TAG_LABELS, tagOf, type ChatTag } from "./tags";
import type { StagedPptx } from "./useChat";
import { TEMPLATE_GREETING, TemplateStart } from "./TemplateStart";
import { MessageTime } from "./MessageTime";
import { AssistantTyping } from "./AssistantTyping";
import { useAssistantTyping } from "./useAssistantTyping";

interface Props {
  officeSelection?: OfficeSelection | null;
  onDismissOfficeSelection?: () => void;
  suggestions?: string[];
  ctx: CardContext;
  onSend: (text: string, files: File[], target: SlideTarget | null) => Promise<void>;
  /** Разбирает брошенные файлы: презентации забирает сразу, остальные возвращает как вложения к сообщению. */
  onAttach: (files: File[]) => File[];
  staged: StagedPptx[];
  onAnswerStaged: (localId: string, answer: PptxAnswer) => void;
  onSelectTemplate: (id: string) => void;
  onUploadTemplate: (file: File) => Promise<void>;
  /** Выбранная метка сверху: «all» — вся лента. Ряд меток живёт в шапке панели. */
  filter?: ChatTag | "all";
  /** Нажали метку на сообщении: лента сужается до этого шага. */
  onTag?: (tag: ChatTag) => void;
}

/**
 * Чат проекта: лента сообщений и карточек шагов, внизу поле ввода с вложениями; файлы можно бросать в любое место панели.
 * PPTX не ждёт отправки: вопрос «шаблон, готовая презентация или материал» появляется в ленте в момент броска, пока файл грузится.
 */
export function ChatPanel({ ctx, onSend, suggestions = [], onAttach, staged, onAnswerStaged, onSelectTemplate, onUploadTemplate, filter = "all", onTag, officeSelection, onDismissOfficeSelection }: Props) {
  const { project, session } = ctx;
  // Выбранный справа слайд — адресат сообщения: чип над полем ввода, крестик снимает адресацию.
  const target = session.slideTarget;
  const [text, setText] = useState("");
  const [pending, setPending] = useState<File[]>([]);
  const [sending, setSending] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);
  const resetRef = useRef<() => void>(null);
  const followRef = useRef(true);
  // Приветствие — виртуальное первое сообщение; серверную историю не меняем.
  const greeting: ChatMessage = { event_id: "greeting", at: project.created_at, role: "assistant", kind: "text", text: TEMPLATE_GREETING };
  const messages = [greeting, ...project.events];
  const typing = useAssistantTyping(project.project_id, messages, project.events.length || project.template_id ? messages.length : 0);
  const presentedGreeting = typing.present(greeting, 0);

  const addFiles = (files: File[]) => {
    const rest = onAttach(files);
    if (rest.length) setPending((p) => [...p, ...rest]);
  };

  const events = project.events.filter((m) => isVisibleProjectMessage(m, session.jobId));
  // Лента прокручивается вниз при новом сообщении и когда сообщение о правке получает результат.
  const count = events.length + staged.length;
  // Метка сужает ленту, но не прячет сам разговор: поле ввода и подсказки остаются на месте.
  const matchesFilter = (m: ChatMessage) => isVisibleProjectMessage(m, session.jobId) && (filter === "all" || tagOf(m) === filter);
  const shown = project.events.flatMap((m, eventIndex) => {
    const index = eventIndex + 1;
    if (!matchesFilter(m)) return [];
    const presented = typing.present(m, index);
    return presented ? [{ message: presented, index }] : [];
  });
  const activeMessage = messages[typing.activeIndex];
  const showTyping = typing.activeIndex !== 0 && (activeMessage ? matchesFilter(activeMessage) : sending && filter === "all");
  const stagedShown = filter === "all" || filter === "template" ? staged : [];
  const settledEdits = session.result?.edits?.length ?? 0;
  useEffect(() => {
    const el = listRef.current;
    if (el && followRef.current) el.scrollTop = el.scrollHeight;
  }, [count, session.result?.status, settledEdits, typing.progress, typing.activeIndex, showTyping]);

  const blocked = session.editorDirty;
  const submit = async () => {
    if (sending || blocked || (!text.trim() && pending.length === 0)) return;
    setSending(true);
    const t = text;
    const f = pending;
    const to = t.trim() ? target : null;
    setText("");
    setPending([]);
    resetRef.current?.();
    try {
      await onSend(t, f, to);
    } finally {
      setSending(false);
    }
  };
  const placeholder = officeSelection ? (officeSelection.objects.length > 1 ? "Что изменить в выбранных объектах или куда их переместить?" : "Что изменить в объекте или куда его переместить?") : target
    ? `Что изменить на слайде ${target.slideIndex + 1}?`
    : count === 0
      ? "Опишите задачу или перетащите файлы…"
      : "Напишите, что изменить, или добавьте файлы…";

  return (
    <Dropzone
      onDrop={addFiles}
      activateOnClick={false}
      styles={{ root: { border: 0, padding: 0, background: "transparent", borderRadius: 0, display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }, inner: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0, pointerEvents: "auto" } }}
      data-testid="chat-dropzone"
    >
      <div className="chat-list" ref={listRef} data-testid="chat-list" onScroll={(e) => {
        const el = e.currentTarget;
        followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 64;
      }}>
        {(filter === "all" || filter === "template") && (
          <div className="chat-msg chat-msg-assistant" data-testid="msg-assistant">
            <TemplateStart project={project} onSelectTemplate={onSelectTemplate} onUploadTemplate={onUploadTemplate} greeting={presentedGreeting?.kind === "text" ? presentedGreeting.text : ""} typing={typing.activeIndex === 0} />
            <MessageTime at={project.created_at} />
          </div>
        )}
        {filter !== "all" && filter !== "template" && shown.length === 0 && stagedShown.length === 0 && (
          <Text size="sm" c="dimmed" data-testid="chat-tag-empty">
            Под меткой «{TAG_LABELS[filter]}» пока пусто.
          </Text>
        )}
        {shown.map(({ message: m, index }) => {
          const hash = TAG_HASH[tagOf(m)];
          return (
            <div key={index} className={`chat-msg chat-msg-${m.role}`} data-testid={`msg-${m.role}`} data-typing={index === typing.activeIndex || undefined} aria-busy={index === typing.activeIndex || undefined}>
              {renderMessage(m, ctx)}
              {/* Метка на самом сообщении: видно, к какому шагу оно относится, и по ней же
                  лента сужается до этого шага. */}
              {hash && (
                <button type="button" className="chat-tag" onClick={() => onTag?.(tagOf(m))} data-testid={`hash-${tagOf(m)}`}>
                  {hash}
                </button>
              )}
              <MessageTime at={m.at} />
            </div>
          );
        })}
        {stagedShown.map((s) => (
          <Fragment key={s.local_id}>
            <div className="chat-msg chat-msg-user" data-testid="msg-user">
              <Badge color="gray" size="sm" leftSection={<IconFile size={11} />} rightSection={<Loader size={10} color="gray" />}>{s.name} · {formatBytes(s.size)}</Badge>
            </div>
            <div className="chat-msg chat-msg-assistant" data-testid="msg-assistant">
              <PptxQuestion name={s.name} resolved={s.answer} uploading onAnswer={(answer) => onAnswerStaged(s.local_id, answer)} testId={`template-question-${s.local_id}`} />
            </div>
          </Fragment>
        ))}
        {showTyping && <AssistantTyping />}
      </div>
      <div className="chat-composer">
        {officeSelection && <Group gap={6} mb={8} data-testid="office-object-target">
          <Text size="xs">Слайд {officeSelection.slide} · {officeSelection.label} · v{officeSelection.revision}</Text>
          <CloseButton size="sm" aria-label="Снять выбор объекта" onClick={onDismissOfficeSelection} />
          <Text size="xs" c="dimmed">{officeSelection.objects.length > 1 ? "Правка только выбранных объектов. Например: «перемести все три правее»." : "Правка только этого объекта. Например: «сократи текст» или «перенеси правее»."}</Text>
        </Group>}
        {suggestions.length > 0 && <Group gap={6} mb="xs" data-testid="chat-suggestions">
          {suggestions.map((option) => <Button key={option} size="compact-xs" variant="light" disabled={sending} onClick={() => setText(option)}>{option}</Button>)}
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
            minRows={1}
            maxRows={6}
            value={text}
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
          <ActionIcon variant="filled" size="lg" onClick={submit} loading={sending} disabled={blocked || (!text.trim() && pending.length === 0)} aria-label="Отправить" data-testid="chat-send"><IconArrowUp size={18} /></ActionIcon>
        </div>
        {blocked ? (
          <Text size="xs" c="orange" mt={6} data-testid="chat-draft-hint">Сначала примените или отмените правки на слайде: черновик редактора ждёт решения.</Text>
        ) : (
          <Text size="xs" c="dimmed" mt={6}>Enter — отправить, Shift+Enter — перенос строки{project.job_id ? ". ИИ-правка PPTX: /edit инструкция" : ""}</Text>
        )}
      </div>
    </Dropzone>
  );
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
