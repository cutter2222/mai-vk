"use client";

import { ActionIcon, Badge, Button, FileButton, Group, Loader, Menu, Text, TextInput, Tooltip } from "@mantine/core";
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
        data-testid="project-title"
      />
      <div style={{ flex: 1 }} />
      {/* Действия проекта одной группой у правого края: состояние, шаблон, файлы. */}
      <div className="editor-header-actions">
        {result && <StatusBadge status={result.status} />}
        {result?.partial && <Badge color="ink" variant="light">частичный результат</Badge>}
        <Menu
          withinPortal
          position="bottom-end"
          shadow="md"
          opened={menuOpen}
          onChange={setMenuOpen}
          width={380}
          // Длинное имя шаблона не должно раздвигать список и уводить его за край окна:
          // ширина списка ограничена окном, подпись сжимается и обрезается многоточием.
          styles={{ dropdown: { maxWidth: "calc(100vw - 24px)" }, itemLabel: { minWidth: 0 } }}
        >
          {/* Menu.Target держит кнопку напрямую. Раньше он был вложен в HoverCard.Target с
              превью шаблона, и два компонента спорили за ссылку на элемент: выпадающий список
              оставался непозиционированным и открывался в левом верхнем углу окна вместо
              кнопки. Миниатюра каждого шаблона и так есть в самом списке. */}
          <Menu.Target>
            <Button
              variant="subtle"
              color="gray"
              size="xs"
              leftSection={analyzing ? <Loader size={12} /> : thumb(currentTemplate) ? <img src={thumb(currentTemplate)} alt="" width={40} height={22} style={{ borderRadius: 3, objectFit: "cover", border: "1px solid var(--line)", display: "block", marginRight: 4 }} data-testid="template-thumb" /> : <IconTemplate size={14} />}
              rightSection={<IconChevronDown size={12} />}
              style={{ fontWeight: 500, color: project.template_id ? "var(--ink)" : undefined, maxWidth: 280 }}
              // Подпись обрезается многоточием и стоит по центру высоты рядом с миниатюрой
              // (блочная подпись прижималась к верху), между ними — заметный зазор.
              styles={{ label: { display: "flex", alignItems: "center", minWidth: 0, gap: 6 } }}
              data-testid="template-menu"
            >
              <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {currentTemplate ? currentTemplate.name.replace(/\.pptx$/i, "") : project.template_id ? "Шаблон" : "Шаблон не выбран"}
              </span>
              {analyzing && <Text component="span" size="xs" c="dimmed">анализируется</Text>}
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Label>Шаблон оформления</Menu.Label>
            {templates.length === 0 && <Menu.Item disabled>Библиотека пуста</Menu.Item>}
            {templates.map((t) => (
              <Menu.Item
                key={t.template_id}
                onClick={() => onSelectTemplate(t.template_id)}
                leftSection={thumb(t) ? <img src={thumb(t)} alt="" width={77} height={43} style={{ borderRadius: 3, objectFit: "cover", border: "1px solid var(--line)", display: "block" }} /> : <div style={{ width: 77, height: 43, borderRadius: 3, background: "var(--mantine-color-gray-1)" }} />}
                rightSection={t.template_id === project.template_id ? <IconCheck size={14} /> : undefined}
                data-testid={`template-option-${t.template_id}`}
              >
                <div style={{ minWidth: 0 }}>
                  <Text size="sm" truncate>{t.name.replace(/\.pptx$/i, "")}</Text>
                  {/* Под именем — первые цвета палитры: стиль шаблона виден без открытия карточки. */}
                  {t.status === "succeeded" && t.colors?.length ? (
                    <Group gap={4} mt={4} wrap="nowrap" aria-label="Цвета шаблона">
                      {t.colors.slice(0, 5).map((hex, i) => (
                        <span key={`${hex}-${i}`} title={hex} style={{ width: 12, height: 12, borderRadius: "50%", background: hex, border: "1px solid var(--line)", display: "block" }} />
                      ))}
                    </Group>
                  ) : t.status !== "succeeded" ? (
                    <Text size="xs" c="dimmed">{t.status === "failed" ? "анализ не удался" : "анализируется"}</Text>
                  ) : null}
                </div>
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

        {/* Разделитель нужен только когда за ним есть действия задания. */}
        {result && <div className="editor-header-sep" />}
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
    </div>
  );
}
