"use client";

import { Button, Container, Group, Modal, SimpleGrid, Stack, Text, TextInput, Title } from "@mantine/core";
import { IconPlus, IconPresentation } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ProjectCard } from "@/components/home/ProjectCard";
import { api, ApiError } from "@/lib/api/client";
import { notifications } from "@mantine/notifications";
import { createProject, deleteProject, refreshProjects, useProjects } from "@/lib/state/projects";

export default function ProjectsPage() {
  const router = useRouter();
  const { items: projects, loaded } = useProjects();
  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const create = async () => {
    setCreating(true);
    try {
      const p = await createProject();
      router.push(`/project?id=${encodeURIComponent(p.project_id)}`);
    } catch (e) {
      notifications.show({ color: "red", title: "Проект не создан", message: e instanceof ApiError ? e.message : "Сервер недоступен" });
    } finally {
      setCreating(false);
    }
  };

  const rename = async () => {
    if (!renaming) return;
    const title = renaming.title.trim() || "Без названия";
    setRenaming(null);
    await api.projects.patch(renaming.id, { title }).catch(() => undefined);
    await refreshProjects();
  };

  const deletingProject = projects.find((p) => p.project_id === deleting);

  return (
    <div className="page-surface">
      <Container size="xl" py="xl">
        <Group justify="space-between" align="flex-end" mb="lg">
          <div>
            <Title order={2} style={{ letterSpacing: "-0.02em" }}>Мои презентации</Title>
            <Text c="dimmed" size="sm" mt={4}>Каждый проект — одна презентация: шаблон, содержание, три варианта вёрстки и аудит.</Text>
          </div>
          <Button leftSection={<IconPlus size={16} />} onClick={() => void create()} loading={creating} data-testid="new-project">Новая презентация</Button>
        </Group>

        {loaded && projects.length === 0 ? (
          <Stack align="center" gap="sm" py={80} data-testid="projects-empty">
            <IconPresentation size={48} stroke={1.2} color="var(--mantine-color-gray-5)" />
            <Title order={4}>Пока нет ни одной презентации</Title>
            <Text c="dimmed" size="sm" ta="center" maw={420}>Создайте проект, загрузите фирменный шаблон и материалы: сервис соберёт презентацию в трёх вариантах и проверит её по правилам шаблона.</Text>
            <Button mt="sm" leftSection={<IconPlus size={16} />} onClick={() => void create()} loading={creating}>Создать первую презентацию</Button>
          </Stack>
        ) : (
          <SimpleGrid cols={{ base: 1, sm: 2, md: 3, lg: 4 }} spacing="lg" data-testid="projects-grid">
            <button type="button" className="project-card-new" onClick={() => void create()} data-testid="new-project-card">
              <IconPlus size={28} stroke={1.5} />
              <Text size="sm" fw={500}>Новая презентация</Text>
            </button>
            {projects.map((p) => (
              <ProjectCard
                key={p.project_id}
                item={p}
                onOpen={() => router.push(`/project?id=${encodeURIComponent(p.project_id)}`)}
                onRename={() => setRenaming({ id: p.project_id, title: p.title })}
                onDelete={() => setDeleting(p.project_id)}
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
              void rename();
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
        <Text size="sm">«{deletingProject?.title}» исчезнет из списка вместе с чатом и ссылками на файлы. Файлы уже созданных заданий на сервере не удаляются.</Text>
        <Group justify="flex-end" mt="md">
          <Button variant="default" onClick={() => setDeleting(null)}>Отмена</Button>
          <Button
            color="red"
            onClick={() => {
              if (deleting) void deleteProject(deleting);
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
