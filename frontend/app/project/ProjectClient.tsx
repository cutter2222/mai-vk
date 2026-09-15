"use client";

import { Button, Container, Stack, Text, Title } from "@mantine/core";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useSyncExternalStore } from "react";

import { ProjectEditor } from "@/components/project/ProjectEditor";
import { useProject } from "@/lib/state/projects";

const subscribeNoop = () => () => {};

export function ProjectClient() {
  const params = useSearchParams();
  const id = params.get("id");
  const project = useProject(id);
  // До гидратации реестр пуст; «не найден» показывается только после чтения localStorage.
  const hydrated = useSyncExternalStore(subscribeNoop, () => true, () => false);

  if (!id || (hydrated && !project)) {
    return (
      <Container size="sm" py={80}>
        <Stack align="flex-start" data-testid="project-empty">
          <Title order={3}>{id ? "Презентация не найдена" : "Не указана презентация"}</Title>
          <Text c="dimmed">{id ? "Проект удалён или создан в другом браузере: реестр проектов хранится локально." : "Откройте презентацию из списка или создайте новую."}</Text>
          <Button component={Link} href="/" variant="light">К моим презентациям</Button>
        </Stack>
      </Container>
    );
  }
  if (!project) return null;
  return <ProjectEditor project={project} />;
}
