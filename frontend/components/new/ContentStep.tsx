"use client";

import { Alert, Badge, Button, Card, Group, Select, SimpleGrid, Stack, Table, TagsInput, Text, TextInput, Textarea, Title } from "@mantine/core";
import { Dropzone } from "@mantine/dropzone";
import { notifications } from "@mantine/notifications";
import { IconFile, IconTrash, IconUpload, IconX } from "@tabler/icons-react";
import { useState } from "react";

import { StatusBadge } from "@/components/common/StatusBadge";
import { api, ApiError, type ContentDetail } from "@/lib/api/client";
import { usePolling } from "@/lib/api/usePolling";
import { formatBytes } from "@/lib/format";

const ACCEPT = {
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
  "text/csv": [".csv"],
  "application/pdf": [".pdf"],
  "text/markdown": [".md"],
  "text/plain": [".txt"],
  "image/png": [".png"],
  "image/jpeg": [".jpg", ".jpeg"],
};

export interface BriefForm {
  purpose: string;
  title: string;
  audience: string;
  goal: string;
  language: string;
  tone: string;
  must_include: string[];
  avoid: string[];
}

const EMPTY_BRIEF: BriefForm = { purpose: "product", title: "", audience: "", goal: "", language: "ru", tone: "", must_include: [], avoid: [] };

interface Props {
  packageId: string | null;
  onPackage: (id: string | null) => void;
}

export function ContentStep({ packageId, onPackage }: Props) {
  const [files, setFiles] = useState<File[]>([]);
  const [brief, setBrief] = useState<BriefForm>(EMPTY_BRIEF);
  const [submitting, setSubmitting] = useState(false);

  const detail = usePolling<ContentDetail>(
    packageId ? () => api.content.get(packageId) : null,
    (d) => d.status === "succeeded" || d.status === "failed",
    [packageId],
  );

  const briefFilled = brief.title.trim().length > 0;
  const canSubmit = (files.length > 0 || briefFilled) && !submitting;

  const submit = async () => {
    setSubmitting(true);
    try {
      const payload = briefFilled ? { ...brief, must_include: brief.must_include, avoid: brief.avoid } : undefined;
      const res = await api.content.create(files, payload);
      onPackage(res.package_id);
      notifications.show({ color: "green", title: "Содержание принято", message: "Импорт занимает несколько секунд. Настройки генерации можно задавать сразу." });
    } catch (e) {
      notifications.show({ color: "red", title: "Импорт не запущен", message: e instanceof ApiError ? e.message : "Неизвестная ошибка", icon: <IconX size={16} /> });
    } finally {
      setSubmitting(false);
    }
  };

  const pkg = detail.data?.package;

  return (
    <Stack gap="md">
      <div>
        <Title order={3}>2. Содержание</Title>
        <Text c="dimmed" size="sm">Файлы контент-пакета, краткий бриф или и то и другое. При заполнении брифа загруженные файлы не теряются.</Text>
      </div>

      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
        <Card>
          <Text fw={600} mb="xs">Контент-пакет</Text>
          <Dropzone
            onDrop={(accepted) => setFiles((prev) => [...prev, ...accepted].slice(0, 20))}
            onReject={() => notifications.show({ color: "red", title: "Файл не подходит", message: "Поддерживаются docx, xlsx, csv, pdf, md, txt и изображения до 50 МБ" })}
            maxSize={50 * 1024 * 1024}
            accept={ACCEPT}
            data-testid="content-dropzone"
          >
            <Group justify="center" gap="sm" mih={70} style={{ pointerEvents: "none" }}>
              <IconUpload size={22} stroke={1.5} />
              <Text size="sm">Перетащите файлы: docx, xlsx, csv, pdf, md, txt, png, jpg</Text>
            </Group>
          </Dropzone>
          {files.length > 0 && (
            <Table mt="sm" verticalSpacing={4} withRowBorders={false}>
              <Table.Tbody>
                {files.map((f, i) => (
                  <Table.Tr key={`${f.name}-${i}`}>
                    <Table.Td><Group gap={6}><IconFile size={14} /><Text size="sm">{f.name}</Text></Group></Table.Td>
                    <Table.Td w={90}><Text size="xs" c="dimmed">{formatBytes(f.size)}</Text></Table.Td>
                    <Table.Td w={40}>
                      <Button variant="subtle" color="gray" size="compact-xs" onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))} aria-label="Убрать файл">
                        <IconTrash size={14} />
                      </Button>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </Card>

        <Card>
          <Text fw={600} mb="xs">Бриф</Text>
          <Stack gap="xs">
            <Group grow>
              <Select
                label="Назначение"
                data={[{ value: "feature", label: "Фича" }, { value: "product", label: "Продукт" }, { value: "project", label: "Проект" }, { value: "initiative", label: "Инициатива" }, { value: "report", label: "Отчёт" }]}
                value={brief.purpose}
                onChange={(v) => setBrief({ ...brief, purpose: v ?? "product" })}
                allowDeselect={false}
              />
              <Select label="Язык" data={[{ value: "ru", label: "Русский" }, { value: "en", label: "English" }]} value={brief.language} onChange={(v) => setBrief({ ...brief, language: v ?? "ru" })} allowDeselect={false} />
            </Group>
            <TextInput label="Название" placeholder="Запуск сервиса умных уведомлений" value={brief.title} onChange={(e) => setBrief({ ...brief, title: e.currentTarget.value })} data-testid="brief-title" />
            <TextInput label="Аудитория" placeholder="руководители продуктовых направлений" value={brief.audience} onChange={(e) => setBrief({ ...brief, audience: e.currentTarget.value })} />
            <Textarea label="Цель" placeholder="получить одобрение на пилот" autosize minRows={1} value={brief.goal} onChange={(e) => setBrief({ ...brief, goal: e.currentTarget.value })} />
            <TextInput label="Тон" placeholder="деловой, уверенный" value={brief.tone} onChange={(e) => setBrief({ ...brief, tone: e.currentTarget.value })} />
            <TagsInput label="Обязательно включить" placeholder="метрики пилота, план на квартал" value={brief.must_include} onChange={(v) => setBrief({ ...brief, must_include: v })} />
            <TagsInput label="Избегать" placeholder="технические детали инфраструктуры" value={brief.avoid} onChange={(v) => setBrief({ ...brief, avoid: v })} />
          </Stack>
        </Card>
      </SimpleGrid>

      <Group>
        <Button onClick={submit} disabled={!canSubmit} loading={submitting} data-testid="content-submit">
          {packageId ? "Импортировать заново" : "Импортировать содержание"}
        </Button>
        {packageId && detail.data && <StatusBadge status={detail.data.status === "succeeded" ? "ready" : detail.data.status} />}
        <Text size="xs" c="dimmed">Режим: {files.length && briefFilled ? "пакет + бриф" : briefFilled ? "бриф" : files.length ? "пакет" : "не выбран"}</Text>
      </Group>

      {detail.error && <Alert color="red" title="Ошибка импорта">{detail.error.message}</Alert>}

      {pkg && (
        <Alert color="green" title="Импорт готов" data-testid="import-summary">
          <Group gap="xs">
            <Badge variant="light">{pkg.blocks.length} блоков</Badge>
            <Badge variant="light">{pkg.facts.length} фактов</Badge>
            <Badge variant="light">{pkg.datasets.length} таблиц</Badge>
            <Badge variant="light">{pkg.assets.length} изображений</Badge>
            <Badge variant="light" color="gray">режим {pkg.mode}</Badge>
          </Group>
          {pkg.missing_data && pkg.missing_data.length > 0 && (
            <Text size="xs" mt="xs" c="dimmed">Не хватает данных: {pkg.missing_data.map((m) => m.what).join(", ")}. Сервис не будет их выдумывать.</Text>
          )}
        </Alert>
      )}
    </Stack>
  );
}
