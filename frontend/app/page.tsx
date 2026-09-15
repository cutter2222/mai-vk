"use client";

import { Button, Container, Group, Modal, SimpleGrid, Stack, Text, TextInput, Title } from "@mantine/core";
import { IconPlus, IconPresentation } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ProjectCard } from "@/components/home/ProjectCard";
import { api, type TemplateListItem } from "@/lib/api/client";
import { createProject, deleteProject, updateProject, useProjects } from "@/lib/state/projects";

export default function ProjectsPage() {
  const router = useRouter();
  const projects = useProjects();
  const [templates, setTemplates] = useState<Record<string, TemplateListItem>>({});
  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    api.templates
      .list()
      .then((list) => setTemplates(Object.fromEntries(list.map((t) => [t.template_id, t]))))
      .catch(() => setTemplates({}));
  }, []);

  const create = () => {
    const p = createProject();
    router.push(`/project?id=${encodeURIComponent(p.id)}`);
  };

  const deletingProject = projects.find((p) => p.id === deleting);

  return (
    <div className="page-surface">
      <Container size="xl" py="xl">
        <Group justify="space-between" align="flex-end" mb="lg">
          <div>
            <Title order={2} style={{ letterSpacing: "-0.02em" }}>Мои презентации</Title>
            <Text c="dimmed" size="sm" mt={4}>Каждый проект — одна презентация: шаблон, содержание, три варианта вёрстки и аудит.</Text>
          </div>
          <Button leftSection={<IconPlus size={16} />} onClick={create} data-testid="new-project">Новая презентация</Button>
        </Group>

        {projects.length === 0 ? (
          <Stack align="center" gap="sm" py={80} data-testid="projects-empty">
            <IconPresentation size={48} stroke={1.2} color="var(--mantine-color-gray-5)" />
            <Title order={4}>Пока нет ни одной презентации</Title>
            <Text c="dimmed" size="sm" ta="center" maw={420}>Создайте проект, загрузите фирменный шаблон и материалы: сервис соберёт презентацию в трёх вариантах и проверит её по правилам шаблона.</Text>
            <Button mt="sm" leftSection={<IconPlus size={16} />} onClick={create}>Создать первую презентацию</Button>
          </Stack>
        ) : (
          <SimpleGrid cols={{ base: 1, sm: 2, md: 3, lg: 4 }} spacing="lg" data-testid="projects-grid">
            <button type="button" className="project-card-new" onClick={create} data-testid="new-project-card">
              <IconPlus size={28} stroke={1.5} />
              <Text size="sm" fw={500}>Новая презентация</Text>
            </button>
            {projects.map((p) => (
              <ProjectCard
                key={p.id}
                project={p}
                templates={templates}
                onOpen={() => router.push(`/project?id=${encodeURIComponent(p.id)}`)}
                onRename={() => setRenaming({ id: p.id, title: p.title })}
                onDelete={() => setDeleting(p.id)}
              />
            ))}
          </SimpleGrid>
        )}
      </Container>

      <Modal opened={renaming !== null} onClose={() => setRenaming(null)} title="Переименовать презентацию" centered>
        {renaming && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              updateProject(renaming.id, { title: renaming.title.trim() || "Без названия" });
              setRenaming(null);
            }}
          >
            <TextInput value={renaming.title} onChange={(e) => setRenaming({ ...renaming, title: e.currentTarget.value })} data-autofocus data-testid="rename-input" />
            <Group justify="flex-end" mt="md">
              <Button variant="default" onClick={() => setRenaming(null)}>Отмена</Button>
              <Button type="submit">Сохранить</Button>
            </Group>
          </form>
        )}
      </Modal>

      <Modal opened={deleting !== null} onClose={() => setDeleting(null)} title="Удалить презентацию?" centered>
        <Text size="sm">«{deletingProject?.title}» исчезнет из списка. Файлы уже созданных заданий на сервере не удаляются.</Text>
        <Group justify="flex-end" mt="md">
          <Button variant="default" onClick={() => setDeleting(null)}>Отмена</Button>
          <Button
            color="red"
            onClick={() => {
              if (deleting) deleteProject(deleting);
              setDeleting(null);
            }}
            data-testid="confirm-delete"
          >
            Удалить
          </Button>
        </Group>
      </Modal>
    </div>
  );
}
