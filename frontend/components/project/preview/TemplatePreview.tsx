"use client";

import { Badge, Group, Loader, Progress, Stack, Text } from "@mantine/core";
import { useState } from "react";

import { api, type TemplateDetail } from "@/lib/api/client";
import { PATTERN_ROLE_LABELS } from "@/lib/format";

import { SlideViewer } from "./SlideViewer";

interface Props {
  templateId: string;
  detail: TemplateDetail | null;
  error: Error | null;
}

/** Оформление выбранного шаблона до генерации: образцы слайдов, палитра и шрифты. */
export function TemplatePreview({ templateId, detail, error }: Props) {
  const [index, setIndex] = useState(0);
  const profile = detail?.profile;

  if (error) {
    return (
      <div className="preview-empty">
        <Stack align="center" gap={6}>
          <Text fw={600}>Не удалось получить профиль шаблона</Text>
          <Text size="sm" c="dimmed">{error.message}</Text>
        </Stack>
      </div>
    );
  }

  if (!profile) {
    return (
      <div className="preview-empty">
        <Stack align="center" gap="xs" maw={420} data-testid="template-analyzing">
          <Loader size="sm" />
          <Text fw={600}>Шаблон анализируется</Text>
          <Progress value={65} animated size="sm" w={260} />
          <Text size="sm" c="dimmed" ta="center">Разбираем образцы, палитру и шрифты. Заполняйте содержание, ждать не нужно: генерация дождётся профиля сама.</Text>
        </Stack>
      </div>
    );
  }

  const slides = profile.patterns.map((p) => ({
    key: p.pattern_id,
    src: p.preview_path ? api.templates.assetUrl(templateId, p.preview_path) : undefined,
    label: `${PATTERN_ROLE_LABELS[p.role] ?? p.role}${p.name ? ` · ${p.name}` : ""}`,
  }));
  const current = profile.patterns[Math.min(index, slides.length - 1)];

  return (
    <>
      <div className="preview-toolbar">
        <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
          <Text fw={600} size="sm">Образцы шаблона</Text>
          <Text size="xs" c="dimmed">{profile.patterns.length} композиций · {profile.stats.slides} слайдов в файле</Text>
        </Group>
      </div>
      <SlideViewer
          slides={slides}
          index={index}
          onIndex={setIndex}
          caption={
            current ? (
              <>
                <Badge color="gray">{PATTERN_ROLE_LABELS[current.role] ?? current.role}</Badge>
                <Text size="sm">{current.name}</Text>
                <Text size="xs" c="dimmed">{current.slots.length} слотов</Text>
              </>
            ) : null
          }
        >
          <Text size="xs" c="dimmed" mt="md" ta="center">Так выглядят образцы шаблона. После генерации здесь появятся слайды презентации в трёх вариантах вёрстки.</Text>
        </SlideViewer>
    </>
  );
}
