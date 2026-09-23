"use client";

import { Button, Container, Loader, Stack, Text, Title } from "@mantine/core";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ProjectEditor } from "@/components/project/ProjectEditor";
import { isDraft, sameProject, startDraft, useProject } from "@/lib/state/projects";

export function ProjectClient() {
  const params = useSearchParams();
  const router = useRouter();
  const urlId = params.get("id");
  // `?new=1` — экран новой презентации: проект живёт в памяти вкладки, пока пользователь
  // ничего не сделал. Черновик один на вкладку, поэтому повторный рендер его не множит.
  const wanted = !urlId && params.get("new") === "1";
  const [draftId] = useState(() => (wanted ? startDraft().project_id : null));
  const id = urlId ?? draftId;
  const { project, status } = useProject(id);

  // Первое действие завело проект на сервере: адрес становится обычной ссылкой на него,
  // чтобы перезагрузка и «поделиться» работали.
  const realId = project && id && isDraft(id) && project.project_id !== id ? project.project_id : null;
  useEffect(() => {
    if (realId) router.replace(`/project?id=${encodeURIComponent(realId)}`);
  }, [realId, router]);

  if (!id || status === "missing") {
    return (
      <Container size="sm" py={80}>
        <Stack align="flex-start" data-testid="project-empty">
          <Title order={3}>{id ? "Презентация не найдена" : "Не указана презентация"}</Title>
          <Text c="dimmed">{id ? "Проект удалён или ссылка неверна." : "Откройте презентацию из списка или создайте новую."}</Text>
          <Button component={Link} href="/" variant="light">К моим презентациям</Button>
        </Stack>
      </Container>
    );
  }
  if (!project) {
    return (
      <Container size="sm" py={80}>
        <Stack align="center" data-testid="project-loading"><Loader size="sm" /><Text c="dimmed" size="sm">Открываем презентацию</Text></Stack>
      </Container>
    );
  }
  // Черновик, ставший проектом на сервере, — та же презентация: редактор не пересоздаётся.
  // Иначе первое действие стирает экран: PPTX ещё грузится, а вопрос о нём уже пропал.
  const editorKey = draftId && sameProject(draftId, project.project_id) ? draftId : project.project_id;
  return <ProjectEditor key={editorKey} project={project} />;
}
