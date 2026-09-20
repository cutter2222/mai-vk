"use client";

import { Stack, Text } from "@mantine/core";
import { IconLayoutDashboard } from "@tabler/icons-react";

import { ApiError, type TemplateDetail } from "@/lib/api/client";
import type { ContentPackage } from "@/lib/api/types";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";
import type { SlideEditor } from "@/lib/hooks/useSlideEditor";
import type { Project } from "@/lib/state/projects";

import { GenerationPreview } from "./GenerationPreview";
import { TemplatePreview } from "./TemplatePreview";

interface Props {
  project: Project;
  session: GenerationSession;
  editor: SlideEditor;
  pkg: ContentPackage | null | undefined;
  templateDetail: TemplateDetail | null;
  templateError: Error | null;
  onChoose: (variantId: string | null) => void;
  onShowAudit: () => void;
}

/** Правая часть редактора: слайды задания, иначе выбранный шаблон, иначе подсказка. */
export function PreviewPane({ project, session, editor, pkg, templateDetail, templateError, onChoose, onShowAudit }: Props) {
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
    return <GenerationPreview session={session} editor={editor} templateDetail={templateDetail} pkg={pkg} projectId={project.project_id} chosenVariant={project.chosen_variant} onChoose={onChoose} onShowAudit={onShowAudit} />;
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
