"use client";

import { Button, Container, Group, Modal, SimpleGrid, Text, TextInput } from "@mantine/core";
import { IconPlus } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { CatalogHeader } from "@/components/common/CatalogHeader";
import { ProjectCard } from "@/components/home/ProjectCard";
import { api } from "@/lib/api/client";
import { deleteProject, refreshProjects, useProjects } from "@/lib/state/projects";

export default function ProjectsPage() {
  const router = useRouter();
  const { items: projects, loaded } = useProjects();
  const [renaming, setRenaming] = useState<{ id: string; title: string } | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");

  // Нажатие открывает пустой экран, а не заводит проект: на сервере он появится с первым
  // действием — сообщением, файлом, шаблоном или названием. Иначе в списке копились пустые
  // «Новые презентации» от тех, кто просто заглянул.
  const create = () => router.push("/project?new=1");

  const rename = async () => {
    if (!renaming) return;
    const title = renaming.title.trim() || "Без названия";
    setRenaming(null);
    await api.projects.patch(renaming.id, { title }).catch(() => undefined);
    await refreshProjects();
  };

  const deletingProject = projects.find((p) => p.project_id === deleting);
  const shown = projects.filter((project) => {
    const projectStatus = project.job_status ?? (project.job_id ? "queued" : "draft");
    return project.title.toLocaleLowerCase("ru").includes(query.trim().toLocaleLowerCase("ru"))
      && (status === "all" || (status === "active" ? ["queued", "running"].includes(projectStatus) : projectStatus === status));
  });

  return (
    <div className="page-surface">
      <Container size="xl" py="xl">
        {/* Создание — первой карточкой в сетке, поэтому отдельной кнопки в шапке нет:
            одно действие, один вход. */}
        <CatalogHeader
          title="Презентации"
          searchLabel="Поиск презентаций"
          searchPlaceholder="Найти презентацию"
          query={query}
          onQueryChange={setQuery}
          filterLabel="Статус презентаций"
          filter={status}
          onFilterChange={setStatus}
          options={[
            { value: "all", label: "Все презентации" },
            { value: "draft", label: "Черновики" },
            { value: "active", label: "В работе" },
            { value: "succeeded", label: "Готовые" },
            { value: "needs_review", label: "Требуют проверки" },
            { value: "failed", label: "С ошибкой" },
            { value: "canceled", label: "Отменённые" },
          ]}
        />

        {/* Отдельного пустого экрана нет: карточка создания и так первая в сетке, а вторая
            кнопка «создать первую» была тем же действием в другом месте. */}
        {loaded && projects.length === 0 ? (
          <Text c="dimmed" size="sm" mb="lg" data-testid="projects-empty" maw={560}>
            Пока пусто. Создайте презентацию, бросьте в чат фирменный шаблон и материалы —
            сервис соберёт её в трёх вариантах вёрстки и проверит по правилам шаблона.
          </Text>
        ) : null}
        {loaded || projects.length ? (
          <SimpleGrid cols={{ base: 1, sm: 2, md: 3, lg: 4 }} spacing="lg" data-testid="projects-grid">
            <button
              type="button"
              className="grid-card-new"
              onClick={create}
              data-testid="new-project"
            >
              <IconPlus size={26} stroke={1.6} />
              <Text size="sm" fw={600}>Новая презентация</Text>
            </button>
            {shown.map((p) => (
              <ProjectCard
                key={p.project_id}
                item={p}
                onOpen={() => router.push(`/project?id=${encodeURIComponent(p.project_id)}`)}
                onRename={() => setRenaming({ id: p.project_id, title: p.title })}
                onDelete={() => setDeleting(p.project_id)}
              />
            ))}
          </SimpleGrid>
        ) : null}
        {loaded && projects.length > 0 && shown.length === 0 && <Text ta="center" c="dimmed" py={60}>По вашему запросу презентаций нет. Измените название или фильтр.</Text>}
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
