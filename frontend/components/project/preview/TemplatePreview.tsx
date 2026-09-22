"use client";

import { Anchor, Badge, ColorSwatch, Group, Loader, Stack, Text, Tooltip } from "@mantine/core";
import { IconExternalLink } from "@tabler/icons-react";

import { useState } from "react";
import { api, type TemplateDetail } from "@/lib/api/client";
import { SlideViewer } from "./SlideViewer";

interface Props {
  templateId: string;
  detail: TemplateDetail | null;
  error: Error | null;
}

/**
 * До генерации показываем готовые миниатюры шаблона без сессии Document Server.
 * Пока анализ не опубликовал миниатюры, остаётся индикатор состояния шаблона.
 */
export function TemplatePreview({ templateId, detail, error }: Props) {
  const [index, setIndex] = useState(0);
  const profile = detail?.profile;
  const name = detail?.name?.replace(/\.pptx$/i, "") ?? templateId;
  const fonts = profile ? [...new Set(profile.design_tokens.typography.fonts.map((f) => f.family))].slice(0, 2).join(", ") : "";
  const palette = profile?.design_tokens.colors.palette.slice(0, 8) ?? [];
  // Manifest также содержит пустые макеты для библиотеки композиций, не слайды файла.
  const previews = (detail?.previews ?? []).filter((path) => /(^|\/)slide-\d+\.png$/.test(path))
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));

  if (previews.length) return <div className="project-office" data-testid="project-template-preview">
    <Text size="xs" c="dimmed" p="xs">Исходный шаблон · только просмотр. После генерации здесь появится превью презентации.</Text>
    <SlideViewer index={index} onIndex={setIndex} caption={<Text size="xs">{name}</Text>}
      slides={previews.map((path, i) => ({ key: path, deckIndex: i, label: `Слайд ${i + 1}`, src: api.templates.assetUrl(templateId, path) }))} />
  </div>;

  return (
    <div className="viewer" data-testid={error ? "template-error" : profile ? "template-ready" : "template-analyzing"}>
      <div className="filmstrip">
        <button type="button" data-active="true" aria-label="Слайд 1" title="Слайд 1">
          <span className="thumb-number">1</span>
          <span className="thumb-image"><span className="slide-frame slide-blank" /></span>
        </button>
      </div>
      <div className="viewer-main">
        <div className="viewer-stage">
          <div className="stage-column">
            <div className="preview-stage">
              <div className="slide-frame slide-blank" data-testid="slide-blank">
                <Text size="sm" c="dimmed">Пока пусто</Text>
              </div>
            </div>
            <div className="viewer-bar">
              <div className="viewer-bar-meta">
                <Text size="sm" fw={500} truncate>{name}</Text>
                {error ? (
                  <Badge size="xs" color="red" variant="light">профиль не получен</Badge>
                ) : profile ? (
                  <Badge size="xs" color="green" variant="light">шаблон выбран</Badge>
                ) : (
                  <Badge size="xs" color="gray" variant="light" leftSection={<Loader size={8} color="gray" />}>анализируется</Badge>
                )}
              </div>
              <Text size="sm" c="dimmed" className="viewer-bar-pager">Слайдов нет</Text>
            </div>
          </div>
        </div>
        <div className="viewer-extra">
          <Stack gap={6}>
            {error ? (
              <Text size="xs" c="dimmed">{error.message}</Text>
            ) : profile ? (
              <Group gap="sm" wrap="nowrap" align="center">
                <Group gap={4} wrap="nowrap">
                  {palette.map((c) => (
                    <Tooltip key={`${c.hex}-${c.role}`} label={`${c.hex} · ${c.role}`}><ColorSwatch color={c.hex} size={12} /></Tooltip>
                  ))}
                </Group>
                <Text size="xs" c="dimmed" truncate>{profile.patterns.length} композиций{fonts ? ` · ${fonts}` : ""}</Text>
                <Anchor href={`/templates?id=${encodeURIComponent(templateId)}`} target="_blank" rel="noreferrer" size="xs" style={{ flex: "0 0 auto" }} data-testid="template-open-library-preview">
                  <Group gap={4} wrap="nowrap"><IconExternalLink size={12} />Что извлечено</Group>
                </Anchor>
              </Group>
            ) : (
              <Text size="xs" c="dimmed">Разбираю образцы, палитру и шрифты. Ждать не нужно: добавляйте материалы и описывайте задачу в чате.</Text>
            )}
            <Text size="xs" c="dimmed">Слайды появятся здесь после генерации: три варианта вёрстки и находки аудита.</Text>
          </Stack>
        </div>
      </div>
    </div>
  );
}
