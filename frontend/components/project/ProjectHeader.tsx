"use client";

import { ActionIcon, Badge, Button, FileButton, Group, HoverCard, Loader, Menu, Text, TextInput, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconArrowLeft, IconCheck, IconChevronDown, IconDownload, IconPlayerStop, IconRefresh, IconTemplate, IconUpload } from "@tabler/icons-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { StatusBadge } from "@/components/common/StatusBadge";
import { api, ApiError, type TemplateListItem } from "@/lib/api/client";
import { downloadArtifact } from "@/lib/download";
import { VARIANT_LABELS } from "@/lib/format";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";
import type { Project } from "@/lib/state/projects";

interface Props {
  project: Project;
  session: GenerationSession;
  onTitle: (title: string) => void;
  onSelectTemplate: (templateId: string) => void;
  onUploadTemplate: (file: File) => void;
}

/** Шапка проекта: возврат к списку, название, состояние задания, отмена, повтор и скачивание. */
export function ProjectHeader({ project, session, onTitle, onSelectTemplate, onUploadTemplate }: Props) {
  const [title, setTitle] = useState(project.title);
  const [prevTitle, setPrevTitle] = useState(project.title);
  if (prevTitle !== project.title) {
    setPrevTitle(project.title);
    setTitle(project.title);
  }
  const commit = () => {
    const next = title.trim() || "Без названия";
    if (next !== project.title) onTitle(next);
    setTitle(next);
  };

  // Список шаблонов библиотеки обновляется при открытии меню, при смене выбранного и пока
  // выбранный ещё анализируется: миниатюра появляется, как только профиль готов.
  const [templates, setTemplates] = useState<TemplateListItem[]>([]);
  const [menuOpen, setMenuOpen] = useState(false);
  const currentTemplate = templates.find((t) => t.template_id === project.template_id);
  const analyzing = Boolean(project.template_id) && (!currentTemplate || currentTemplate.status === "queued" || currentTemplate.status === "running");
  useEffect(() => {
    let cancelled = false;
    const load = () => api.templates.list().then((t) => { if (!cancelled) setTemplates(t); }).catch(() => { if (!cancelled) setTemplates([]); });
    void load();
    const timer = analyzing ? setInterval(load, 2000) : null;
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [project.template_id, menuOpen, analyzing]);
  const thumb = (t?: TemplateListItem) => (t?.preview ? api.templates.assetUrl(t.template_id, t.preview) : undefined);
  // Миниатюры — готовые PNG с нашего API, как в SlideImage; next/image им не нужен.
  /* eslint-disable @next/next/no-img-element */

  const { result, variant } = session;
  const filesReady = Boolean(variant?.artifacts?.pptx);
  const auditRunning = variant?.audit?.status === "running" || variant?.audit?.status === "pending";
  const download = (name: string, ext: string) => async () => {
    if (!session.jobId || !variant) return;
    try {
      await downloadArtifact(api.generations.artifactUrl(session.jobId, name), `${project.title}-${variant.variant_id}-r${variant.revision}.${ext}`);
    } catch (e) {
      notifications.show({ color: "red", title: "Файл не скачан", message: e instanceof ApiError ? e.message : "Неизвестная ошибка" });
    }
  };

  return (
    <div className="editor-header">
      <Tooltip label="Мои презентации">
        <ActionIcon component={Link} href="/" variant="subtle" color="gray" aria-label="К списку презентаций" data-testid="back-home">
          <IconArrowLeft size={18} />
        </ActionIcon>
      </Tooltip>
      <TextInput
        variant="unstyled"
        className="editor-title-input"
        value={title}
        onChange={(e) => setTitle(e.currentTarget.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.currentTarget as HTMLInputElement).blur();
          if (e.key === "Escape") {
            setTitle(project.title);
            (e.currentTarget as HTMLInputElement).blur();
          }
        }}
        aria-label="Название презентации"
        style={{ flex: "0 1 420px", minWidth: 160 }}
        data-testid="project-title"
      />
      <Menu withinPortal position="bottom-start" shadow="none" opened={menuOpen} onChange={setMenuOpen} width={360}>
        <HoverCard withinPortal position="bottom-start" shadow="md" openDelay={250} disabled={!thumb(currentTemplate) || menuOpen}>
          <HoverCard.Target>
            <div style={{ display: "inline-flex" }}>
              <Menu.Target>
                <Button
                  variant="subtle"
                  color="gray"
                  size="xs"
                  leftSection={analyzing ? <Loader size={12} /> : thumb(currentTemplate) ? <img src={thumb(currentTemplate)} alt="" width={36} height={20} style={{ borderRadius: 3, objectFit: "cover", border: "1px solid var(--mantine-color-gray-3)", display: "block" }} data-testid="template-thumb" /> : <IconTemplate size={14} />}
                  rightSection={<IconChevronDown size={12} />}
                  style={{ fontWeight: 500, color: project.template_id ? "var(--mantine-color-graphite-8)" : undefined }}
                  data-testid="template-menu"
                >
                  {currentTemplate ? currentTemplate.name.replace(/\.pptx$/i, "") : project.template_id ? "Шаблон" : "Шаблон не выбран"}
                  {analyzing && <Text component="span" size="xs" c="dimmed" ml={6}>анализируется</Text>}
                </Button>
              </Menu.Target>
            </div>
          </HoverCard.Target>
          <HoverCard.Dropdown p={6} data-testid="template-hover-preview">
            <img src={thumb(currentTemplate)} alt="Первый слайд шаблона" width={320} style={{ display: "block", borderRadius: 4, border: "1px solid var(--mantine-color-gray-3)" }} />
            <Text size="xs" c="dimmed" mt={4}>{currentTemplate?.slide_count ?? "—"} слайдов · {currentTemplate?.pattern_count ?? "—"} композиций</Text>
          </HoverCard.Dropdown>
        </HoverCard>
        <Menu.Dropdown>
          <Menu.Label>Шаблон оформления</Menu.Label>
          {templates.length === 0 && <Menu.Item disabled>Библиотека пуста</Menu.Item>}
          {templates.map((t) => (
            <Menu.Item
              key={t.template_id}
              onClick={() => onSelectTemplate(t.template_id)}
              leftSection={thumb(t) ? <img src={thumb(t)} alt="" width={64} height={36} style={{ borderRadius: 3, objectFit: "cover", border: "1px solid var(--mantine-color-gray-3)", display: "block" }} /> : <div style={{ width: 64, height: 36, borderRadius: 3, background: "var(--mantine-color-gray-1)" }} />}
              rightSection={t.template_id === project.template_id ? <IconCheck size={14} /> : undefined}
              data-testid={`template-option-${t.template_id}`}
            >
              <Group gap={0} style={{ flexDirection: "column", alignItems: "flex-start" }}>
                <Text size="sm" truncate>{t.name.replace(/\.pptx$/i, "")}</Text>
                <Text size="xs" c="dimmed">{t.status === "succeeded" ? `${t.slide_count ?? "—"} слайдов в файле` : t.status === "failed" ? "анализ не удался" : "анализируется"}</Text>
              </Group>
            </Menu.Item>
          ))}
          <Menu.Divider />
          <FileButton onChange={(file) => file && onUploadTemplate(file)} accept=".pptx">
            {(props) => <Menu.Item {...props} leftSection={<IconUpload size={14} />} closeMenuOnClick data-testid="template-upload">Загрузить другой PPTX</Menu.Item>}
          </FileButton>
          <Menu.Item component={Link} href={project.template_id ? `/templates?id=${encodeURIComponent(project.template_id)}` : "/templates"} leftSection={<IconTemplate size={14} />}>
            {project.template_id ? "Что извлечено из шаблона" : "Библиотека шаблонов"}
          </Menu.Item>
        </Menu.Dropdown>
      </Menu>
      {/* eslint-enable @next/next/no-img-element */}
      {result && <StatusBadge status={result.status} />}
      {result?.partial && <Badge color="yellow" variant="light">частичный результат</Badge>}
      <div style={{ flex: 1 }} />
      {result && !session.terminal && (
        <Button variant="light" color="red" size="xs" leftSection={<IconPlayerStop size={14} />} onClick={session.cancel} loading={session.busy} data-testid="cancel">
          Отменить
        </Button>
      )}
      {result && (result.status === "failed" || result.status === "canceled" || result.partial) && (
        <Button variant="light" size="xs" leftSection={<IconRefresh size={14} />} onClick={session.retry} loading={session.busy} data-testid="retry">
          Повторить
        </Button>
      )}
      {result && variant && (
        <Menu withinPortal position="bottom-end" disabled={!filesReady} shadow="md">
          <Menu.Target>
            <Button size="xs" leftSection={<IconDownload size={14} />} disabled={!filesReady} data-testid="download-menu">
              Скачать{auditRunning && filesReady ? " (аудит ещё идёт)" : ""}
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Label>{VARIANT_LABELS[variant.variant_id] ?? variant.variant_id} · ревизия {variant.revision}</Menu.Label>
            {variant.artifacts?.pptx && <Menu.Item onClick={download(variant.artifacts.pptx, "pptx")} data-testid="dl-pptx">PPTX, нативные объекты</Menu.Item>}
            {variant.artifacts?.pdf && <Menu.Item onClick={download(variant.artifacts.pdf, "pdf")} data-testid="dl-pdf">PDF</Menu.Item>}
            {variant.artifacts?.html && <Menu.Item onClick={download(variant.artifacts.html, "html")} data-testid="dl-html">HTML, автономный просмотр</Menu.Item>}
            {project.chosen_variant && project.chosen_variant !== variant.variant_id && (
              <Text size="xs" c="dimmed" px="sm" py={4}>Для демонстрации отмечен вариант «{VARIANT_LABELS[project.chosen_variant]}»</Text>
            )}
          </Menu.Dropdown>
        </Menu>
      )}
    </div>
  );
}
