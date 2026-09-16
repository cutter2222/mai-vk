"use client";

import { ActionIcon, Group, Menu, Text } from "@mantine/core";
import { IconDotsVertical, IconPencil, IconPresentation, IconTrash } from "@tabler/icons-react";

import { API_BASE, type ProjectListItem } from "@/lib/api/client";
import { formatDate, STATUS_LABELS } from "@/lib/format";

interface Props {
  item: ProjectListItem;
  onOpen: () => void;
  onRename: () => void;
  onDelete: () => void;
}

/** Карточка проекта: состояние задания, миниатюра, шаблон и дата приходят из списка проектов с сервера. */
export function ProjectCard({ item, onOpen, onRename, onDelete }: Props) {
  const src = item.thumbnail_url ? item.thumbnail_url.replace(/^\/api/, API_BASE) : null;
  const meta = [item.template_name?.replace(/\.pptx$/i, "") ?? "шаблон не выбран", item.slide_count ? `${item.slide_count} слайдов` : null, formatDate(item.updated_at)].filter(Boolean).join(" · ");
  const status = item.job_status ?? (item.job_id ? "queued" : null);
  const tone = status === "succeeded" ? "ok" : status === "needs_review" ? "warn" : status === "failed" || status === "canceled" ? "bad" : undefined;
  const statusLabel = status ? (STATUS_LABELS[status] ?? status) : "Черновик";

  return (
    <div className="grid-card" role="button" tabIndex={0} onClick={onOpen} onKeyDown={(e) => e.key === "Enter" && onOpen()} data-testid={`project-card-${item.project_id}`}>
      <div className="grid-card-thumb">
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={src} alt="" />
        ) : (
          <div className="grid-card-thumb-empty">
            <IconPresentation size={36} stroke={1.2} />
          </div>
        )}
        <div className="grid-card-menu">
          <Menu withinPortal position="bottom-end" shadow="md">
            <Menu.Target>
              <ActionIcon variant="default" size="sm" aria-label="Действия" onClick={(e) => e.stopPropagation()} data-testid={`project-menu-${item.project_id}`}>
                <IconDotsVertical size={14} />
              </ActionIcon>
            </Menu.Target>
            <Menu.Dropdown onClick={(e) => e.stopPropagation()}>
              <Menu.Item leftSection={<IconPencil size={14} />} onClick={onRename}>Переименовать</Menu.Item>
              <Menu.Item leftSection={<IconTrash size={14} />} color="red" onClick={onDelete} data-testid={`project-delete-${item.project_id}`}>Удалить</Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </div>
      </div>
      <div className="grid-card-body">
        <Text fw={600} size="sm" truncate title={item.title}>{item.title}</Text>
        <Group gap={6} wrap="nowrap" mt={4} style={{ minWidth: 0 }}>
          <span className="quiet-status" data-tone={tone} style={{ fontSize: 12 }} data-testid={status ? `status-${status}` : undefined}><i />{statusLabel}</span>
          <Text size="xs" c="dimmed" truncate>· {meta}</Text>
        </Group>
      </div>
    </div>
  );
}
