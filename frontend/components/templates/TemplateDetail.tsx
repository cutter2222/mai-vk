"use client";

import { ActionIcon, Alert, Button, Container, Loader, Menu, Stack, Text, Title, Tooltip } from "@mantine/core";
import { IconAlertTriangle, IconArrowLeft, IconDots, IconDownload, IconExternalLink, IconFileTypePpt, IconListDetails, IconPalette, IconTrash } from "@tabler/icons-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { StatusBadge } from "@/components/common/StatusBadge";
import { api, ApiError, type TemplateDetail as Detail } from "@/lib/api/client";
import { usePolling } from "@/lib/api/usePolling";
import { formatBytes, formatDate } from "@/lib/format";

import { DeleteTemplateModal } from "./DeleteTemplateModal";
import { DesignCodeSection } from "./sections/DesignCodeSection";
import { DesignSystemSection } from "./sections/DesignSystemSection";
import { DigestSection } from "./sections/DigestSection";
import { StructureSection } from "./sections/StructureSection";
import { templateTitle } from "./TemplateCard";
import { TemplateSourceViewer } from "./TemplateSourceViewer";

const DONE = new Set(["succeeded", "failed"]);

/** Карточка шаблона: что извлёк анализ, по вкладкам. Пока анализ идёт, страница опрашивает сервер. */
type Section = "style" | "details" | "source";

const SECTION_COPY: Record<Exclude<Section, "source">, [string, string]> = {
  style: ["Дизайн-система", "Цвета, типографика, геометрия и правила оформления из вашего файла. На их основе создаются новые слайды."],
  details: ["О файле", "Ресурсы, макеты и технические сведения о шаблоне. Здесь можно проверить результаты анализа и предупреждения."],
};

export function TemplateDetail({ templateId }: { templateId: string }) {
  const router = useRouter();
  const detail = usePolling<Detail>(() => api.templates.get(templateId), (d) => DONE.has(d.status), [templateId]);
  const [deleting, setDeleting] = useState(false);
  const [section, setSection] = useState<Section>("style");
  const [officeEnabled, setOfficeEnabled] = useState(false);
  useEffect(() => {
    let cancelled = false;
    void api.office.capabilities().then((value) => { if (!cancelled) setOfficeEnabled(value.enabled); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);
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

  // SDK загружается только по запросу пользователя в разделе «Слайды».
  const SECTIONS: Array<{ key: Section; label: string; icon: React.ReactNode; count?: number }> = [
    { key: "style", label: "Стиль", icon: <IconPalette size={16} stroke={1.7} /> },
    ...(officeEnabled ? [{ key: "source" as const, label: "Слайды", icon: <IconFileTypePpt size={16} stroke={1.7} />, count: profile?.stats.slides }] : []),
    { key: "details", label: "О файле", icon: <IconListDetails size={16} stroke={1.7} /> },
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
        {officeEnabled && profile && <Button size="xs" variant="light" leftSection={<IconFileTypePpt size={16} />} onClick={() => setSection(section === "source" ? "style" : "source")} data-testid="template-open-source">{section === "source" ? "К стилю" : "Смотреть слайды"}</Button>}
        {/* JSON и удаление в ТЗ не требуются: это наши функции, поэтому они в меню. */}
        <Menu withinPortal position="bottom-end" shadow="md">
          <Menu.Target>
            <ActionIcon variant="subtle" color="gray" aria-label="Действия с шаблоном" data-testid="template-actions">
              <IconDots size={18} />
            </ActionIcon>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item component="a" href={api.templates.sourceUrl(templateId)} leftSection={<IconDownload size={14} />}>Скачать исходный PPTX</Menu.Item>
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
            <Text size="sm" c="dimmed" ta="center" maw={420}>Рендерим слайды, собираем палитру, шрифты и композиции. Страница обновится сама.</Text>
          </Stack>
        </div>
      ) : (
        <div className="tpl-body">
          <nav className="tpl-nav" aria-label="Разделы шаблона">
            <Text size="xs" c="dimmed" fw={600} tt="uppercase" px="sm" mb="sm">Содержимое шаблона</Text>
            {SECTIONS.map((item) => (
              <button
                key={item.key}
                type="button"
                className="tpl-nav-item"
                data-active={section === item.key || undefined}
                aria-current={section === item.key ? "page" : undefined}
                onClick={() => setSection(item.key)}
                data-testid={`section-${item.key}`}
              >
                {item.icon}
                <span>{item.label}</span>
                {item.count ? <b>{item.count}</b> : null}
              </button>
            ))}
            <div className="tpl-nav-note"><Text size="xs" c="dimmed">Анализ исходного PPTX</Text><Text size="sm" fw={600} mt={6}>{profile.stats.slides} слайдов · {templatePatterns.length} композиций</Text><Text size="xs" c="dimmed" mt={8}>Шаблон доступен для выбора в любой презентации.</Text></div>
          </nav>

          <main className="tpl-content" data-source={section === "source" || undefined}>
            {section !== "source" && <div className="tpl-section-heading">
              <Text size="xs" fw={600} c="dimmed" tt="uppercase" mb={6}>Шаблон / {SECTIONS.find((item) => item.key === section)?.label}</Text>
              <Title order={2}>{SECTION_COPY[section][0]}</Title>
              <Text size="sm" c="dimmed" mt="xs" maw={760}>{SECTION_COPY[section][1]}</Text>
            </div>}
            {section === "style" && (
              <Stack gap={28}>
                <DesignCodeSection profile={profile} />
                <DesignSystemSection profile={profile} />
              </Stack>
            )}
            {section === "details" && (
              <Stack gap={24}>
                <StructureSection profile={profile} templateId={templateId} />
                <DigestSection profile={profile} />
              </Stack>
            )}
            {section === "source" && <TemplateSourceViewer templateId={templateId} />}
          </main>
        </div>
      )}

      <DeleteTemplateModal target={deleting ? { template_id: templateId, name: data.name } : null} onClose={() => setDeleting(false)} onDeleted={() => router.push("/templates")} />
    </div>
  );
}
