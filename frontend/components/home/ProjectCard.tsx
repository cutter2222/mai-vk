"use client";

import { ActionIcon, Group, Menu, Text } from "@mantine/core";
import { IconDotsVertical, IconPencil, IconPresentation, IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { api, TERMINAL_STATES, type TemplateListItem } from "@/lib/api/client";
import type { GenerationResult } from "@/lib/api/types";
import { formatDate, STATUS_LABELS } from "@/lib/format";
import type { Project } from "@/lib/state/projects";

interface Props {
  project: Project;
  templates: Record<string, TemplateListItem>;
  onOpen: () => void;
  onRename: () => void;
  onDelete: () => void;
}

/** Карточка проекта: миниатюра первого слайда, состояние задания, шаблон и дата. */
export function ProjectCard({ project, templates, onOpen, onRename, onDelete }: Props) {
  const [result, setResult] = useState<GenerationResult | null>(null);
  const [templateThumb, setTemplateThumb] = useState<string | null>(null);

  // Результат задания без частого опроса: обновление раз в несколько секунд, пока задание идёт.
  useEffect(() => {
    if (!project.job_id) return;
    const jobId = project.job_id;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const load = async () => {
      try {
        const r = await api.generations.get(jobId);
        if (!alive) return;
        setResult(r);
        if (!TERMINAL_STATES.has(r.status)) timer = setTimeout(load, 4000);
      } catch {
        /* задание недоступно: карточка показывает состояние черновика */
      }
    };
    void load();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [project.job_id]);

  // Пока слайдов нет, миниатюра берётся из первого образца шаблона.
  useEffect(() => {
    if (!project.template_id || project.job_id) return;
    const id = project.template_id;
    let alive = true;
    api.templates
      .get(id)
      .then((d) => {
        const preview = d.profile?.patterns.find((p) => p.preview_path)?.preview_path;
        if (alive && preview) setTemplateThumb(api.templates.assetUrl(id, preview));
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [project.template_id, project.job_id]);

  const variant = result?.variants.find((v) => v.variant_id === project.chosen_variant && v.artifacts?.thumbnails?.length) ?? result?.variants.find((v) => v.artifacts?.thumbnails?.length);
  const firstThumb = variant?.artifacts?.thumbnails?.[0];
  const src = firstThumb && project.job_id ? api.generations.artifactUrl(project.job_id, firstThumb.name) : templateThumb;
  const template = project.template_id ? templates[project.template_id] : undefined;
  const slides = variant?.slide_count ?? variant?.artifacts?.thumbnails?.length;

  const meta = [template?.name?.replace(/\.pptx$/i, "") ?? "шаблон не выбран", slides ? `${slides} слайдов` : null, formatDate(project.updated_at)].filter(Boolean).join(" · ");
  const status = result?.status ?? (project.job_id ? "queued" : null);
  const tone = status === "succeeded" ? "ok" : status === "needs_review" ? "warn" : status === "failed" || status === "canceled" ? "bad" : undefined;
  const statusLabel = status ? (STATUS_LABELS[status] ?? status) : "Черновик";

  return (
    <div className="project-card" role="button" tabIndex={0} onClick={onOpen} onKeyDown={(e) => e.key === "Enter" && onOpen()} data-testid={`project-card-${project.id}`}>
      <div className="project-thumb">
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={src} alt="" />
        ) : (
          <div className="project-thumb-empty">
            <IconPresentation size={36} stroke={1.2} />
          </div>
        )}
        <div className="project-menu">
          <Menu withinPortal position="bottom-end" shadow="md">
            <Menu.Target>
              <ActionIcon variant="default" size="sm" aria-label="Действия" onClick={(e) => e.stopPropagation()} data-testid={`project-menu-${project.id}`}>
                <IconDotsVertical size={14} />
              </ActionIcon>
            </Menu.Target>
            <Menu.Dropdown onClick={(e) => e.stopPropagation()}>
              <Menu.Item leftSection={<IconPencil size={14} />} onClick={onRename}>Переименовать</Menu.Item>
              <Menu.Item leftSection={<IconTrash size={14} />} color="red" onClick={onDelete} data-testid={`project-delete-${project.id}`}>Удалить</Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </div>
      </div>
      <div className="project-body">
        <Text fw={600} size="sm" truncate title={project.title}>{project.title}</Text>
        <Group gap={6} wrap="nowrap" mt={4} style={{ minWidth: 0 }}>
          <span className="quiet-status" data-tone={tone} style={{ fontSize: 12 }} data-testid={status ? `status-${status}` : undefined}><i />{statusLabel}</span>
          <Text size="xs" c="dimmed" truncate>· {meta}</Text>
        </Group>
      </div>
    </div>
  );
}
