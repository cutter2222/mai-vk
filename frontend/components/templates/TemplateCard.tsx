"use client";

import { ActionIcon, Group, Loader, Menu, Text } from "@mantine/core";
import { IconDotsVertical, IconExternalLink, IconTemplate, IconTrash } from "@tabler/icons-react";

import { api, type TemplateListItem } from "@/lib/api/client";
import { formatDate } from "@/lib/format";

interface Props {
  item: TemplateListItem;
  onOpen: () => void;
  onDelete: () => void;
}

export const templateTitle = (name: string) => name.replace(/\.pptx$/i, "");

/** Карточка библиотеки: миниатюра первого образца, имя, состояние анализа и объём файла. */
export function TemplateCard({ item, onOpen, onDelete }: Props) {
  const src = item.preview ? api.templates.assetUrl(item.template_id, item.preview) : null;
  const analyzing = item.status === "queued" || item.status === "running";
  const tone = item.status === "succeeded" ? "ok" : item.status === "failed" ? "bad" : "warn";
  const statusLabel = item.status === "succeeded" ? "Разобран" : item.status === "failed" ? "Анализ не удался" : "Анализируется";
  const meta = [
    item.slide_count ? `${item.slide_count} слайдов` : null,
    item.pattern_count ? `${item.pattern_count} композиций` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="grid-card" role="button" tabIndex={0} onClick={onOpen} onKeyDown={(e) => e.key === "Enter" && onOpen()} data-testid={`template-card-${item.template_id}`}>
      <div className="grid-card-thumb">
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={src} alt="" />
        ) : (
          <div className="grid-card-thumb-empty">{analyzing ? <Loader size="sm" color="gray" /> : <IconTemplate size={36} stroke={1.2} />}</div>
        )}
        <div className="grid-card-menu">
          <Menu withinPortal position="bottom-end" shadow="md">
            <Menu.Target>
              <ActionIcon variant="default" size="sm" aria-label="Действия" onClick={(e) => e.stopPropagation()} data-testid={`template-card-menu-${item.template_id}`}>
                <IconDotsVertical size={14} />
              </ActionIcon>
            </Menu.Target>
            <Menu.Dropdown onClick={(e) => e.stopPropagation()}>
              <Menu.Item leftSection={<IconExternalLink size={14} />} onClick={onOpen}>Открыть</Menu.Item>
              <Menu.Item leftSection={<IconTrash size={14} />} color="red" onClick={onDelete} data-testid={`template-delete-${item.template_id}`}>Удалить из библиотеки</Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </div>
      </div>
      <div className="grid-card-body">
        <Text fw={600} size="sm" truncate title={item.name}>{templateTitle(item.name)}</Text>
        <Group gap={6} wrap="nowrap" mt={5} style={{ minWidth: 0 }}>
          <span className="quiet-status" data-tone={tone} style={{ fontSize: 12 }} data-testid={`template-status-${item.status}`}><i />{statusLabel}</span>
          {meta && <Text size="xs" c="dimmed" truncate>· {meta}</Text>}
        </Group>
        <Text size="xs" c="dimmed" mt={2}>{formatDate(item.created_at)}</Text>
      </div>
    </div>
  );
}
