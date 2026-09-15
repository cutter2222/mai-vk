"use client";

import { ActionIcon, Badge, CloseButton, FileButton, Group, Stack, Text, Textarea, Tooltip } from "@mantine/core";
import { Dropzone } from "@mantine/dropzone";
import { IconArrowUp, IconFile, IconPaperclip } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";

import { formatBytes } from "@/lib/format";
import type { ChatMessage } from "@/lib/state/projects";

import { AuditCard, BriefCard, ContentCard, JobCard, TemplateCard, TemplateQuestionCard, type CardContext } from "./cards";

interface Props {
  ctx: CardContext;
  onSend: (text: string, files: File[]) => Promise<void>;
}

/** Чат проекта: лента сообщений и карточек шагов, внизу поле ввода с вложениями; файлы можно бросать в любое место панели. */
export function ChatPanel({ ctx, onSend }: Props) {
  const { project } = ctx;
  const [text, setText] = useState("");
  const [pending, setPending] = useState<File[]>([]);
  const [sending, setSending] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);
  const resetRef = useRef<() => void>(null);

  const count = project.messages.length;
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [count, ctx.session.result?.status]);

  const submit = async () => {
    if (sending || (!text.trim() && pending.length === 0)) return;
    setSending(true);
    const t = text;
    const f = pending;
    setText("");
    setPending([]);
    resetRef.current?.();
    try {
      await onSend(t, f);
    } finally {
      setSending(false);
    }
  };

  return (
    <Dropzone
      onDrop={(files) => setPending((p) => [...p, ...files])}
      activateOnClick={false}
      styles={{ root: { border: 0, padding: 0, background: "transparent", borderRadius: 0, display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }, inner: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0, pointerEvents: "auto" } }}
      data-testid="chat-dropzone"
    >
      <div className="chat-list" ref={listRef} data-testid="chat-list">
        {count === 0 && (
          <div className="chat-intro" data-testid="chat-intro">
            <Text fw={600} mb={6}>Соберём презентацию в фирменном стиле</Text>
            <Text size="sm" c="dimmed">Перетащите сюда PPTX-шаблон компании и материалы: документы, таблицы, картинки. Опишите задачу одной фразой — например, «сделай презентацию про запуск сервиса умных уведомлений для руководителей, чтобы одобрили пилот».</Text>
            <Text size="sm" c="dimmed" mt={6}>Шаблон задаёт оформление, материалы — содержание. Я соберу 10–15 слайдов в трёх вариантах вёрстки, проверю их и покажу справа.</Text>
          </div>
        )}
        {project.messages.map((m) => (
          <div key={m.id} className={`chat-msg chat-msg-${m.role}`} data-testid={`msg-${m.role}`}>
            {renderMessage(m, ctx)}
          </div>
        ))}
      </div>
      <div className="chat-composer">
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
          <FileButton resetRef={resetRef} multiple onChange={(files) => setPending((p) => [...p, ...files])} accept=".pptx,.docx,.xlsx,.csv,.pdf,.md,.txt,.png,.jpg,.jpeg,.mp4,.mov">
            {(props) => (
              <Tooltip label="Прикрепить шаблон или материалы">
                <ActionIcon {...props} variant="subtle" color="gray" size="lg" aria-label="Прикрепить файлы" data-testid="chat-attach"><IconPaperclip size={18} /></ActionIcon>
              </Tooltip>
            )}
          </FileButton>
          <Textarea
            variant="unstyled"
            placeholder={count === 0 ? "Опишите задачу или перетащите файлы…" : "Напишите, что изменить, или добавьте файлы…"}
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
          <ActionIcon variant="filled" size="lg" onClick={submit} loading={sending} disabled={!text.trim() && pending.length === 0} aria-label="Отправить" data-testid="chat-send"><IconArrowUp size={18} /></ActionIcon>
        </div>
        <Text size="xs" c="dimmed" mt={6}>Enter — отправить, Shift+Enter — новая строка. Файлы можно бросать в чат.</Text>
      </div>
    </Dropzone>
  );
}

function renderMessage(m: ChatMessage, ctx: CardContext) {
  if (m.role === "user") {
    const files = ctx.project.files.filter((f) => m.file_ids.includes(f.id));
    return (
      <Stack gap={6} align="flex-end">
        {files.length > 0 && (
          <Group gap={6} justify="flex-end">
            {files.map((f) => <Badge key={f.id} color="gray" size="sm" leftSection={<IconFile size={11} />}>{f.name}</Badge>)}
          </Group>
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
    case "audit_card":
      return <AuditCard m={m} ctx={ctx} />;
    default:
      return null;
  }
}
