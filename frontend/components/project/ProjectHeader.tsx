"use client";

import { ActionIcon, Badge, Button, TextInput, Tooltip } from "@mantine/core";
import { IconArrowLeft, IconPlayerStop, IconRefresh } from "@tabler/icons-react";
import Link from "next/link";
import { useState, type ReactNode, type Ref } from "react";

import { StatusBadge } from "@/components/common/StatusBadge";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";
import type { Project } from "@/lib/state/projects";

interface Props {
  project: Project;
  session: GenerationSession;
  onTitle: (title: string) => void;
  officeActionsRef?: Ref<HTMLDivElement>;
  /** Выбор шаблона оформления: всегда в правом углу шапки. */
  templatePicker?: ReactNode;
}

/** Шапка проекта: возврат к списку, название, состояние задания, отмена, повтор и скачивание. */
export function ProjectHeader({ project, session, onTitle, officeActionsRef, templatePicker }: Props) {
  const [title, setTitle] = useState(project.title);
  const [prevTitle, setPrevTitle] = useState(project.title);
  if (prevTitle !== project.title) {
    setPrevTitle(project.title);
    setTitle(project.title);
  }
  const commit = () => {
    const next = title.trim() || "Без названия";
    if (next !== project.title) onTitle(next);
    setTitle(next);
  };

  const { result } = session;

  return (
    <div className="editor-header">
      <Tooltip label="Мои презентации">
        <ActionIcon component={Link} href="/" variant="subtle" color="gray" aria-label="К списку презентаций" data-testid="back-home">
          <IconArrowLeft size={18} />
        </ActionIcon>
      </Tooltip>
      <TextInput
        variant="unstyled"
        className="editor-title-input"
        value={title}
        onChange={(e) => setTitle(e.currentTarget.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.currentTarget as HTMLInputElement).blur();
          if (e.key === "Escape") {
            setTitle(project.title);
            (e.currentTarget as HTMLInputElement).blur();
          }
        }}
        aria-label="Название презентации"
        data-testid="project-title"
      />
      <div style={{ flex: 1 }} />
      {/* Действия проекта одной группой у правого края: состояние, файлы и шаблон. */}
      <div className="editor-header-actions">
        {result && <StatusBadge status={result.status} />}
        {result?.partial && <Badge color="ink" variant="light">частичный результат</Badge>}
        {/* Состояние отделено от всего, что правее: кнопок задания, редактора и шаблона. */}
        {result && <div className="editor-header-sep" />}
        {result && !session.terminal && (
          <Button variant="light" color="red" size="xs" leftSection={<IconPlayerStop size={14} />} onClick={session.cancel} loading={session.busy} data-testid="cancel">
            Отменить
          </Button>
        )}
        {result && (result.status === "failed" || result.status === "canceled" || result.partial) && (
          <Button variant="light" size="xs" leftSection={<IconRefresh size={14} />} onClick={session.retry} loading={session.busy} data-testid="retry">
            Повторить
          </Button>
        )}
        <div ref={officeActionsRef} className="office-header-actions" />
        {/* Шаблон — в самом углу: кнопки задания и редактора появляются левее и его не двигают. */}
        {templatePicker && <div className="editor-header-sep" />}
        {templatePicker}
      </div>
    </div>
  );
}
