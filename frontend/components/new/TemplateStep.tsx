"use client";

import { Alert, Badge, Card, ColorSwatch, Group, Loader, Progress, SimpleGrid, Stack, Text, Title, Tooltip } from "@mantine/core";
import { Dropzone } from "@mantine/dropzone";
import { notifications } from "@mantine/notifications";
import { IconCheck, IconFileTypePpt, IconUpload, IconX } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { SlideImage } from "@/components/common/SlideImage";
import { StatusBadge } from "@/components/common/StatusBadge";
import { api, ApiError, type TemplateDetail, type TemplateListItem } from "@/lib/api/client";
import { usePolling } from "@/lib/api/usePolling";
import { formatBytes, PATTERN_ROLE_LABELS } from "@/lib/format";

const MAX_MB = 100;

interface Props {
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}

export function TemplateStep({ selectedId, onSelect }: Props) {
  const [templates, setTemplates] = useState<TemplateListItem[]>([]);
  const [uploading, setUploading] = useState(false);

  const reload = () => api.templates.list().then(setTemplates).catch(() => setTemplates([]));
  useEffect(() => {
    void reload();
  }, []);

  const detail = usePolling<TemplateDetail>(
    selectedId ? () => api.templates.get(selectedId) : null,
    (d) => d.status === "succeeded" || d.status === "failed",
    [selectedId],
  );

  const upload = async (files: File[]) => {
    const file = files[0];
    if (!file) return;
    setUploading(true);
    try {
      const res = await api.templates.upload(file);
      notifications.show({ color: res.cached ? "gray" : "green", title: res.cached ? "Шаблон уже загружался" : "Шаблон принят", message: res.cached ? "Используем готовый профиль без повторного анализа" : "Анализ начался. Можно переходить к содержанию, ждать не нужно.", icon: <IconCheck size={16} /> });
      await reload();
      onSelect(res.template_id);
    } catch (e) {
      const message = e instanceof ApiError ? e.message : "Не удалось загрузить шаблон";
      notifications.show({ color: "red", title: "Загрузка не выполнена", message, icon: <IconX size={16} /> });
    } finally {
      setUploading(false);
    }
  };

  const profile = detail.data?.profile;

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={3}>1. Шаблон</Title>
          <Text c="dimmed" size="sm">Загрузите PPTX с фирменным оформлением. Сервис извлечёт палитру, шрифты и композиции слайдов.</Text>
        </div>
      </Group>

      <Dropzone
        onDrop={upload}
        onReject={(rejections) => {
          const r = rejections[0];
          const code = r?.errors[0]?.code;
          notifications.show({ color: "red", title: "Файл не подходит", message: code === "file-too-large" ? `Файл больше ${MAX_MB} МБ` : "Нужен файл в формате PPTX" });
        }}
        maxSize={MAX_MB * 1024 * 1024}
        accept={{ "application/vnd.openxmlformats-officedocument.presentationml.presentation": [".pptx"] }}
        multiple={false}
        loading={uploading}
        data-testid="template-dropzone"
      >
        <Group justify="center" gap="md" mih={90} style={{ pointerEvents: "none" }}>
          <IconUpload size={28} stroke={1.5} />
          <div>
            <Text fw={500}>Перетащите PPTX или нажмите, чтобы выбрать</Text>
            <Text size="xs" c="dimmed">Только .pptx, до {MAX_MB} МБ. Шаблоны организаторов весят 13–34 МБ.</Text>
          </div>
        </Group>
      </Dropzone>

      {templates.length > 0 && (
        <SimpleGrid cols={{ base: 1, md: 3 }} spacing="sm">
          {templates.map((t) => (
            <Card
              key={t.template_id}
              padding="sm"
              onClick={() => onSelect(t.template_id)}
              style={{ cursor: "pointer", borderColor: selectedId === t.template_id ? "var(--mantine-color-blue-6)" : undefined }}
              data-testid={`template-card-${t.template_id}`}
            >
              <Group justify="space-between" wrap="nowrap">
                <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
                  <IconFileTypePpt size={20} stroke={1.5} />
                  <Text size="sm" fw={500} truncate>{t.name}</Text>
                </Group>
                <StatusBadge status={t.status === "succeeded" ? "ready" : t.status} />
              </Group>
              <Text size="xs" c="dimmed" mt={4}>{t.slide_count ? `${t.slide_count} слайдов в шаблоне` : "анализируется"}</Text>
            </Card>
          ))}
        </SimpleGrid>
      )}

      {selectedId && detail.data && detail.data.status !== "succeeded" && (
        <Alert color="blue" icon={<Loader size={16} />} title="Шаблон анализируется">
          <Progress value={65} animated size="sm" mt={4} mb={6} />
          <Text size="sm">Разбираем образцы, палитру и шрифты. Переходите к содержанию, генерацию можно запустить сразу: она дождётся профиля сама.</Text>
        </Alert>
      )}

      {detail.error && (
        <Alert color="red" title="Не удалось получить профиль">{detail.error.message}</Alert>
      )}

      {profile && (
        <Card data-testid="template-profile">
          <Group justify="space-between" mb="sm">
            <Text fw={600}>Профиль шаблона</Text>
            <Group gap="xs">
              <Badge variant="light">{profile.patterns.length} паттернов</Badge>
              <Badge variant="light" color="gray">{profile.stats.slides} слайдов</Badge>
              <Badge variant="light" color="gray">{profile.source_file.size_bytes ? formatBytes(profile.source_file.size_bytes) : ""}</Badge>
            </Group>
          </Group>
          <SimpleGrid cols={{ base: 1, md: 3 }} spacing="lg">
            <div>
              <Text size="xs" c="dimmed" mb={6}>Палитра</Text>
              <Group gap={6}>
                {profile.design_tokens.colors.palette.slice(0, 8).map((c) => (
                  <Tooltip key={c.hex} label={`${c.hex} · ${c.role}`} withArrow>
                    <ColorSwatch color={c.hex} size={24} />
                  </Tooltip>
                ))}
              </Group>
            </div>
            <div>
              <Text size="xs" c="dimmed" mb={6}>Шрифты</Text>
              <Stack gap={2}>
                {profile.design_tokens.typography.fonts.slice(0, 4).map((f) => (
                  <Text key={f.family} size="sm">
                    {f.family} <Text span c="dimmed" size="xs">{f.roles?.join(", ")}{f.embedded ? " · встроен" : ""}</Text>
                  </Text>
                ))}
              </Stack>
            </div>
            <div>
              <Text size="xs" c="dimmed" mb={6}>Шкала кеглей</Text>
              <Group gap={6}>
                {profile.design_tokens.typography.scale.map((s) => (
                  <Badge key={`${s.size_pt}-${s.role}`} variant="outline" color="gray">{s.size_pt} pt · {s.role}</Badge>
                ))}
              </Group>
            </div>
          </SimpleGrid>
          <Text size="xs" c="dimmed" mt="md" mb={6}>Композиции из образцов шаблона</Text>
          <div className="thumb-strip">
            {profile.patterns.map((p) => (
              <div key={p.pattern_id} style={{ flex: "0 0 200px" }}>
                <SlideImage src={p.preview_path ? api.templates.assetUrl(profile.template_id, p.preview_path) : undefined} alt={p.name ?? p.role} />
                <Text size="xs" mt={4} truncate>{PATTERN_ROLE_LABELS[p.role] ?? p.role} · {p.name}</Text>
              </div>
            ))}
          </div>
        </Card>
      )}
    </Stack>
  );
}
