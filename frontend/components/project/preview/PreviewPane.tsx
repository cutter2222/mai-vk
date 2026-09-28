"use client";

import { Loader, Stack, Text } from "@mantine/core";
import { IconLayoutDashboard } from "@tabler/icons-react";

import { ApiError } from "@/lib/api/client";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";
import type { Project } from "@/lib/state/projects";

import { GenerationProgress } from "./GenerationProgress";

interface Props {
  project: Project;
  session: GenerationSession;
  officeEnabled: boolean;
  starting: boolean;
}

/**
 * Правая часть проекта — место результата, не образцов шаблона. Выбор шаблона оформления живёт
 * в правом углу шапки, а не здесь: пока результата нет, панель только подсказывает, где он.
 */
export function PreviewPane({ project, session, officeEnabled, starting }: Props) {
  if (starting) return <GenerationProgress session={session} starting />;
  if (project.job_id) {
    if (session.job.error instanceof ApiError && [404, 410].includes(session.job.error.status) && !session.result) {
      return (
        <div className="preview-empty">
          <Stack align="center" gap={6} maw={460} data-testid="job-missing">
            <Text fw={600} component="h3" m={0}>Задание не найдено</Text>
            <Text size="sm" c="dimmed" ta="center">{session.job.error.message}</Text>
            <Text size="xs" c="dimmed" ta="center">Проверьте содержание и запустите генерацию заново: проект сохранит новое задание.</Text>
          </Stack>
        </div>
      );
    }
    // Ответа о задании ещё нет — тихое ожидание, а не «Делаем магию»: у готового проекта следом сразу откроется редактор.
    if (!session.result && !session.job.error) return <div className="preview-empty" data-testid="job-connecting"><Loader size="sm" /></div>;
    if (!session.terminal) return <GenerationProgress session={session} />;
    return (
      <div className="preview-empty">
        <Stack align="center" gap="xs" maw={440} data-testid="presentation-status">
          <IconLayoutDashboard size={44} stroke={1.2} color="var(--ink2)" />
          <Text fw={600}>{session.result?.status === "canceled" ? "Генерация отменена" : session.result?.status === "failed" ? "Не удалось собрать презентацию" : session.variant?.artifacts?.pptx ? "Редактор недоступен" : "Презентация пока не готова"}</Text>
          <Text size="sm" c="dimmed" ta="center">
            {session.variant?.artifacts?.pptx && !officeEnabled ? "Не удалось подключить ONLYOFFICE. Проверьте доступность сервиса и обновите страницу."
              : "Подробности — в чате. Повторить сборку можно в шапке проекта."}
          </Text>
        </Stack>
      </div>
    );
  }

  return (
    <div className="preview-empty">
      {/* Кнопки «Перейти в чат» здесь нет: чат открыт в соседней колонке, и она вела бы
          в то же место, где пользователь уже находится. */}
      <Stack align="center" gap="md" maw={440} data-testid="preview-empty">
        <IconLayoutDashboard size={44} stroke={1.2} color="var(--ink2)" opacity={0.5} />
        <Stack align="center" gap={4}>
          <Text fw={600}>Здесь появится ваша презентация</Text>
          <Text size="sm" c="dimmed" ta="center">
            {project.template_id
              ? "Опишите задачу в чате слева и добавьте материалы — здесь появятся слайды, созданные ИИ в этом шаблоне."
              : "Выберите шаблон оформления вверху справа и опишите задачу в чате слева — здесь появятся слайды, созданные ИИ."}
          </Text>
        </Stack>
      </Stack>
    </div>
  );
}
