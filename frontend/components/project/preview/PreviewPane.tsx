"use client";

import { Stack, Text } from "@mantine/core";
import { IconLayoutDashboard } from "@tabler/icons-react";

import { ApiError, type TemplateDetail } from "@/lib/api/client";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";
import type { Project } from "@/lib/state/projects";

import { TemplatePreview } from "./TemplatePreview";

interface Props {
  project: Project;
  session: GenerationSession;
  officeEnabled: boolean;
  templateDetail: TemplateDetail | null;
  templateError: Error | null;
}

/** Правая часть редактора: слайды задания, иначе выбранный шаблон, иначе подсказка. */
export function PreviewPane({ project, session, officeEnabled, templateDetail, templateError }: Props) {
  if (project.job_id) {
    if (session.job.error && !session.result) {
      const notFound = session.job.error instanceof ApiError && session.job.error.status === 404;
      return (
        <div className="preview-empty">
          <Stack align="center" gap={6} maw={460} data-testid="job-missing">
            <Text fw={600} component="h3" m={0}>{notFound ? "Задание не найдено" : "Не удалось загрузить задание"}</Text>
            <Text size="sm" c="dimmed" ta="center">{session.job.error.message}</Text>
            <Text size="xs" c="dimmed" ta="center">Проверьте содержание и запустите генерацию заново: проект сохранит новое задание.</Text>
          </Stack>
        </div>
      );
    }
    if (!session.result) {
      return (
        <div className="preview-empty">
          <Text c="dimmed">Загружаем задание…</Text>
        </div>
      );
    }
    return (
      <div className="preview-empty">
        <Stack align="center" gap="xs" maw={440} data-testid="presentation-status">
          <IconLayoutDashboard size={44} stroke={1.2} color="var(--ink2)" />
          <Text fw={600}>{!session.terminal ? "Собираем презентацию" : session.variant?.artifacts?.pptx ? "Редактор недоступен" : "Презентация пока не готова"}</Text>
          <Text size="sm" c="dimmed" ta="center">
            {!session.terminal ? "Ход генерации — в чате. Готовая презентация откроется здесь автоматически."
              : session.variant?.artifacts?.pptx && !officeEnabled ? "Не удалось подключить ONLYOFFICE. Проверьте доступность сервиса и обновите страницу."
              : "Подробности — в чате. Повторить сборку можно в шапке проекта."}
          </Text>
        </Stack>
      </div>
    );
  }

  if (project.template_id) {
    return <TemplatePreview templateId={project.template_id} detail={templateDetail} error={templateError} />;
  }

  return (
    <div className="preview-empty">
      {/* Кнопки «Перейти в чат» здесь нет: чат открыт в соседней колонке, и она вела бы
          в то же место, где пользователь уже находится. */}
      <Stack align="center" gap="xs" maw={440} data-testid="preview-empty">
        <IconLayoutDashboard size={44} stroke={1.2} color="var(--ink2)" opacity={0.5} />
        <Text fw={600}>Здесь появятся слайды</Text>
        <Text size="sm" c="dimmed" ta="center">Бросьте в чат шаблон PPTX и материалы и опишите задачу: слайды соберутся в трёх вариантах вёрстки.</Text>
      </Stack>
    </div>
  );
}
