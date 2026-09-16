"use client";

import { ActionIcon, Alert, Anchor, Button, Container, Group, Loader, Progress, Stack, Tabs, Text, Title, Tooltip } from "@mantine/core";
import { IconAlertTriangle, IconArrowLeft, IconExternalLink, IconTrash } from "@tabler/icons-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { StatusBadge } from "@/components/common/StatusBadge";
import { api, ApiError, type TemplateDetail as Detail } from "@/lib/api/client";
import { usePolling } from "@/lib/api/usePolling";
import { formatBytes, formatDate } from "@/lib/format";

import { DeleteTemplateModal } from "./DeleteTemplateModal";
import { DesignSystemSection } from "./sections/DesignSystemSection";
import { DigestSection, JsonSection } from "./sections/DigestSection";
import { PatternsSection } from "./sections/PatternsSection";
import { SlidesSection } from "./sections/SlidesSection";
import { StructureSection } from "./sections/StructureSection";
import { templateTitle } from "./TemplateCard";

const DONE = new Set(["succeeded", "failed"]);

/** Карточка шаблона: что извлёк анализ, по вкладкам. Пока анализ идёт, страница опрашивает сервер. */
export function TemplateDetail({ templateId }: { templateId: string }) {
  const router = useRouter();
  const detail = usePolling<Detail>(() => api.templates.get(templateId), (d) => DONE.has(d.status), [templateId]);
  const [deleting, setDeleting] = useState(false);
  const data = detail.data;
  const profile = data?.profile;

  if (detail.error && !data) {
    const missing = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <Container size="sm" py={80}>
        <Stack align="flex-start" data-testid="template-missing">
          <Title order={3}>{missing ? "Шаблон не найден" : "Не удалось открыть шаблон"}</Title>
          <Text c="dimmed">{missing ? "Шаблон удалён из библиотеки или ссылка неверна." : detail.error.message}</Text>
          <Button component={Link} href="/templates" variant="light">К библиотеке</Button>
        </Stack>
      </Container>
    );
  }
  if (!data) {
    return (
      <Container size="sm" py={80}>
        <Stack align="center"><Loader size="sm" /><Text c="dimmed" size="sm">Открываем шаблон</Text></Stack>
      </Container>
    );
  }

  const meta = profile
    ? [`${profile.stats.slides} слайдов в файле`, `${profile.patterns.length} композиций`, `${profile.layouts.length} макетов`, `${profile.assets.length} ресурсов`, formatBytes(profile.source_file.size_bytes), profile.created_at ? formatDate(profile.created_at) : null].filter(Boolean).join(" · ")
    : null;

  return (
    <div className="page-surface">
      <Container size="xl" py="xl">
        <Group justify="space-between" align="flex-start" mb="lg" wrap="nowrap" data-testid="template-detail">
          <Group gap="sm" wrap="nowrap" align="flex-start" style={{ minWidth: 0 }}>
            <Tooltip label="К библиотеке">
              <ActionIcon component={Link} href="/templates" variant="subtle" color="gray" aria-label="К библиотеке шаблонов" mt={4} data-testid="back-templates">
                <IconArrowLeft size={18} />
              </ActionIcon>
            </Tooltip>
            <div style={{ minWidth: 0 }}>
              <Group gap="sm" wrap="nowrap">
                <Title order={2} style={{ letterSpacing: "-0.02em" }} lineClamp={1} title={data.name}>{templateTitle(data.name)}</Title>
                <StatusBadge status={data.status === "succeeded" ? "ready" : data.status} />
              </Group>
              <Text c="dimmed" size="sm" mt={4}>{meta ?? (data.status === "failed" ? "Профиль не собран." : "Профиль появится, когда закончится анализ.")}</Text>
            </div>
          </Group>
          <Group gap="xs" wrap="nowrap" style={{ flex: "0 0 auto" }}>
            <Anchor href={api.templates.detailUrl(templateId)} target="_blank" rel="noreferrer" size="sm" c="dimmed"><Group gap={4} wrap="nowrap"><IconExternalLink size={14} />JSON</Group></Anchor>
            <Button variant="default" size="xs" color="red" leftSection={<IconTrash size={14} />} onClick={() => setDeleting(true)} data-testid="template-delete">Удалить</Button>
          </Group>
        </Group>

        {data.status === "failed" && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />} title="Анализ не удался" mb="lg" data-testid="template-failed">
            {data.error?.message ?? "Сервер не сообщил причину."}{data.error?.code ? ` (${data.error.code})` : ""} Загрузите файл в проект ещё раз: шаблон будет разобран заново.
          </Alert>
        )}

        {!profile && data.status !== "failed" && (
          <Stack align="center" gap="xs" py={80} data-testid="template-analyzing">
            <Loader size="sm" />
            <Text fw={600}>Шаблон анализируется</Text>
            <Progress value={65} animated size="sm" w={260} />
            <Text size="sm" c="dimmed" ta="center" maw={440}>Рендерим слайды, классифицируем образцы, собираем палитру, шрифты и композиции. Страница обновится сама.</Text>
          </Stack>
        )}

        {profile && (
          <Tabs defaultValue="patterns" keepMounted={false} data-testid="template-tabs">
            <Tabs.List mb="lg">
              <Tabs.Tab value="patterns">Композиции · {profile.patterns.length}</Tabs.Tab>
              <Tabs.Tab value="slides">Слайды файла · {profile.sample_slides?.length ?? data.previews.length}</Tabs.Tab>
              <Tabs.Tab value="design">Дизайн-система</Tabs.Tab>
              <Tabs.Tab value="structure">Макеты и ресурсы</Tabs.Tab>
              <Tabs.Tab value="digest">Для модели{profile.warnings?.length ? ` · ${profile.warnings.length} ⚠` : ""}</Tabs.Tab>
              <Tabs.Tab value="json">JSON</Tabs.Tab>
            </Tabs.List>
            <Tabs.Panel value="patterns"><PatternsSection templateId={templateId} profile={profile} /></Tabs.Panel>
            <Tabs.Panel value="slides"><SlidesSection templateId={templateId} profile={profile} previews={data.previews} /></Tabs.Panel>
            <Tabs.Panel value="design"><DesignSystemSection profile={profile} /></Tabs.Panel>
            <Tabs.Panel value="structure"><StructureSection profile={profile} /></Tabs.Panel>
            <Tabs.Panel value="digest"><DigestSection profile={profile} /></Tabs.Panel>
            <Tabs.Panel value="json"><JsonSection templateId={templateId} profile={profile} /></Tabs.Panel>
          </Tabs>
        )}
      </Container>

      <DeleteTemplateModal target={deleting ? { template_id: templateId, name: data.name } : null} onClose={() => setDeleting(false)} onDeleted={() => router.push("/templates")} />
    </div>
  );
}
