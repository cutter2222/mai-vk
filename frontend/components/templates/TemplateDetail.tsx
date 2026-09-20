"use client";

import { ActionIcon, Alert, Button, Container, Loader, Menu, Progress, Stack, Text, Title, Tooltip } from "@mantine/core";
import { IconAlertTriangle, IconArrowLeft, IconDots, IconExternalLink, IconLayoutBoard, IconLayoutGrid, IconListDetails, IconPalette, IconTrash } from "@tabler/icons-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { StatusBadge } from "@/components/common/StatusBadge";
import { api, ApiError, type TemplateDetail as Detail } from "@/lib/api/client";
import { usePolling } from "@/lib/api/usePolling";
import { formatBytes, formatDate } from "@/lib/format";

import { DeleteTemplateModal } from "./DeleteTemplateModal";
import { DesignCodeSection } from "./sections/DesignCodeSection";
import { DesignSystemSection } from "./sections/DesignSystemSection";
import { DigestSection } from "./sections/DigestSection";
import { PatternsSection } from "./sections/PatternsSection";
import { SlidesSection } from "./sections/SlidesSection";
import { StructureSection } from "./sections/StructureSection";
import { templateTitle } from "./TemplateCard";

const DONE = new Set(["succeeded", "failed"]);

/** Карточка шаблона: что извлёк анализ, по вкладкам. Пока анализ идёт, страница опрашивает сервер. */
type Section = "style" | "slides" | "patterns" | "details";

export function TemplateDetail({ templateId }: { templateId: string }) {
  const router = useRouter();
  const detail = usePolling<Detail>(() => api.templates.get(templateId), (d) => DONE.has(d.status), [templateId]);
  const [deleting, setDeleting] = useState(false);
  const [section, setSection] = useState<Section>("style");
  const data = detail.data;
  const profile = data?.profile;
  // Собственные композиции библиотеки приходят в профиле рядом с паттернами файла
  // (source.kind = "builtin"). Здесь показывается только то, что извлечено из загрузки.
  const templatePatterns = (profile?.patterns ?? []).filter((p) => p.source?.kind !== "builtin");

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
    ? [`${profile.stats.slides} слайдов`, formatBytes(profile.source_file.size_bytes), profile.created_at ? formatDate(profile.created_at) : null].filter(Boolean).join(" · ")
    : null;

  /* Разделы по делу, а не по устройству разбора. Раньше их было шесть, и они назывались
     внутренними понятиями («макеты и ресурсы», «для модели», «подробности разбора») — читалось
     как приборная панель. Осталось три: как выглядит, из чего состоит, чем можно верстать.
     Технические подробности собраны в четвёртый, служебный. */
  const SECTIONS: Array<{ key: Section; label: string; icon: React.ReactNode; count?: number }> = [
    { key: "style", label: "Стиль", icon: <IconPalette size={16} stroke={1.7} /> },
    {
      key: "slides",
      label: "Слайды",
      icon: <IconLayoutGrid size={16} stroke={1.7} />,
      count: profile?.sample_slides?.length ?? data?.previews.length,
    },
    { key: "patterns", label: "Композиции", icon: <IconLayoutBoard size={16} stroke={1.7} />, count: templatePatterns.length },
    { key: "details", label: "Разбор", icon: <IconListDetails size={16} stroke={1.7} /> },
  ];

  return (
    <div className="tpl-page" data-testid="template-detail">
      <header className="tpl-head">
        <Tooltip label="К библиотеке">
          <ActionIcon component={Link} href="/templates" variant="subtle" color="gray" aria-label="К библиотеке шаблонов" data-testid="back-templates">
            <IconArrowLeft size={18} />
          </ActionIcon>
        </Tooltip>
        <Text fw={600} fz={15} lineClamp={1} title={data.name}>{templateTitle(data.name)}</Text>
        <StatusBadge status={data.status === "succeeded" ? "ready" : data.status} />
        <Text size="xs" c="dimmed" lineClamp={1} className="tpl-meta">{meta}</Text>
        <div style={{ flex: 1 }} />
        {/* JSON и удаление в ТЗ не требуются: это наши функции, поэтому они в меню. */}
        <Menu withinPortal position="bottom-end" shadow="md">
          <Menu.Target>
            <ActionIcon variant="subtle" color="gray" aria-label="Действия с шаблоном" data-testid="template-actions">
              <IconDots size={18} />
            </ActionIcon>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item component="a" href={api.templates.detailUrl(templateId)} target="_blank" rel="noreferrer" leftSection={<IconExternalLink size={14} />}>
              JSON профиля
            </Menu.Item>
            <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={() => setDeleting(true)} data-testid="template-delete">
              Удалить из библиотеки
            </Menu.Item>
          </Menu.Dropdown>
        </Menu>
      </header>

      {data.status === "failed" ? (
        <div className="tpl-single">
          <Alert color="red" icon={<IconAlertTriangle size={16} />} title="Анализ не удался" data-testid="template-failed" maw={620}>
            {data.error?.message ?? "Сервер не сообщил причину."}{data.error?.code ? ` (${data.error.code})` : ""} Загрузите файл ещё раз: шаблон будет разобран заново.
          </Alert>
        </div>
      ) : !profile ? (
        <div className="tpl-single">
          <Stack align="center" gap="xs" data-testid="template-analyzing">
            <Loader size="sm" />
            <Text fw={600}>Шаблон анализируется</Text>
            <Progress value={65} animated size="sm" w={260} />
            <Text size="sm" c="dimmed" ta="center" maw={420}>Рендерим слайды, собираем палитру, шрифты и композиции. Страница обновится сама.</Text>
          </Stack>
        </div>
      ) : (
        <div className="tpl-body">
          <nav className="tpl-nav" aria-label="Разделы шаблона">
            {SECTIONS.map((item) => (
              <button
                key={item.key}
                type="button"
                className="tpl-nav-item"
                data-active={section === item.key || undefined}
                onClick={() => setSection(item.key)}
                data-testid={`section-${item.key}`}
              >
                {item.icon}
                <span>{item.label}</span>
                {item.count ? <b>{item.count}</b> : null}
              </button>
            ))}
          </nav>

          <main className="tpl-content">
            {section === "style" && <DesignCodeSection profile={profile} />}
            {section === "slides" && <SlidesSection templateId={templateId} profile={profile} previews={data.previews} />}
            {section === "patterns" && (
              <PatternsSection templateId={templateId} profile={{ ...profile, patterns: templatePatterns }} />
            )}
            {section === "details" && (
              <Stack gap={44}>
                <DesignSystemSection profile={profile} />
                <StructureSection profile={profile} />
                <DigestSection profile={profile} />
              </Stack>
            )}
          </main>
        </div>
      )}

      <DeleteTemplateModal target={deleting ? { template_id: templateId, name: data.name } : null} onClose={() => setDeleting(false)} onDeleted={() => router.push("/templates")} />
    </div>
  );
}
