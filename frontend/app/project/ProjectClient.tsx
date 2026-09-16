"use client";

import { Button, Container, Loader, Stack, Text, Title } from "@mantine/core";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { ProjectEditor } from "@/components/project/ProjectEditor";
import { useProject } from "@/lib/state/projects";

export function ProjectClient() {
  const params = useSearchParams();
  const id = params.get("id");
  const { project, status } = useProject(id);

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
  return <ProjectEditor project={project} />;
}
