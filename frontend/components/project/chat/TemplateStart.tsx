"use client";

import { FileButton, Group, Loader, Menu, Stack, Text, UnstyledButton } from "@mantine/core";
import { IconCheck, IconChevronDown, IconPlus, IconTemplate } from "@tabler/icons-react";
import Link from "next/link";
import { useEffect, useRef, useState, type FocusEvent, type KeyboardEvent } from "react";
import { api, type TemplateListItem } from "@/lib/api/client";
import type { Project } from "@/lib/state/projects";
import styles from "./TemplateStart.module.css";
import { AssistantTyping } from "./AssistantTyping";

export const TEMPLATE_GREETING = "Добавьте шаблон презентации или выберите уже ранее загруженный.";

/** Первый шаг чата; остаётся доступным и для смены шаблона. */
export function TemplateStart({ project, onSelectTemplate, onUploadTemplate, greeting, typing }: {
  project: Project;
  onSelectTemplate: (id: string) => void;
  onUploadTemplate: (file: File) => Promise<void>;
  greeting: string;
  typing: boolean;
}) {
  const [uploading, setUploading] = useState(false);
  const upload = async (file: File | null) => {
    if (!file || uploading) return;
    setUploading(true);
    try { await onUploadTemplate(file); } finally { setUploading(false); }
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
  // Стрелки на кнопке открывают список и ведут на первый или последний пункт (паттерн menu button).
  // Mantine монтирует список через кадр и затем двумя таймерами ставит фокус на заглушку в его
  // начале, поэтому стрелка сразу после Enter иначе достаётся кнопке и теряется. Пока список
  // открыт, каждый перенос фокуса на заглушку перенаправляется на нужный пункт: без таймеров.
  const pendingFocus = useRef<"first" | "last" | null>(null);
  useEffect(() => {
    if (!menuOpen) pendingFocus.current = null;
  }, [menuOpen]);
  const arrowTarget = (key: string) => (key === "ArrowDown" ? "first" : key === "ArrowUp" ? "last" : null);
  const onTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const which = arrowTarget(event.key);
    if (!which) return;
    event.preventDefault();
    pendingFocus.current = which;
    if (!menuOpen) setMenuOpen(true);
  };
  const isPlaceholder = (node: EventTarget) => node instanceof HTMLElement && node.hasAttribute("data-autofocus");
  const onDropdownKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const which = arrowTarget(event.key);
    if (which && isPlaceholder(event.target)) pendingFocus.current = which;
  };
  const onDropdownFocus = (event: FocusEvent<HTMLDivElement>) => {
    const which = pendingFocus.current;
    if (!which || !isPlaceholder(event.target)) return;
    const items = event.currentTarget.querySelectorAll<HTMLElement>("[data-menu-item]:not(:disabled)");
    items[which === "first" ? 0 : items.length - 1]?.focus();
  };
  const thumb = (t?: TemplateListItem) => (t?.preview ? api.templates.assetUrl(t.template_id, t.preview) : undefined);
  // Миниатюры — готовые PNG с нашего API, как в SlideImage; next/image им не нужен.
  /* eslint-disable @next/next/no-img-element */

  return (
    <Stack gap="xs" data-testid="template-start">
      <Text size="sm" className="chat-assistant-text" data-testid="template-greeting" aria-busy={typing} data-typing={typing || undefined}>{greeting}</Text>
      {typing && <AssistantTyping />}
      {/* FileButton остаётся смонтированным после закрытия меню, пока открыт выбор файла. */}
      <FileButton onChange={(file) => void upload(file)} accept=".pptx">
        {(uploadProps) => (
        <Menu
          withinPortal
          position="bottom-start"
          shadow="md"
          opened={menuOpen}
          onChange={setMenuOpen}
          width="target"
          // Длинное имя шаблона не должно раздвигать список и уводить его за край окна:
          // ширина списка ограничена окном, подпись сжимается и обрезается многоточием.
          styles={{ dropdown: { maxWidth: "calc(100vw - 24px)" }, itemLabel: { minWidth: 0 } }}
        >
          <Menu.Target>
            <UnstyledButton
              className={styles.trigger}
              disabled={uploading}
              aria-busy={uploading}
              onKeyDown={onTriggerKeyDown}
              data-testid="template-menu"
            >
              <span className={styles.preview}>
                {uploading || analyzing ? <Loader size={20} /> : thumb(currentTemplate) ? (
                  <img src={thumb(currentTemplate)} alt="" data-testid="template-thumb" />
                ) : <IconTemplate size={24} stroke={1.5} />}
              </span>
              <span className={styles.copy}>
                <span className={styles.caption}>Шаблон оформления</span>
                <span className={styles.name}>
                  {uploading ? "Загружаем шаблон…" : currentTemplate ? currentTemplate.name.replace(/\.pptx$/i, "") : project.template_id ? "Шаблон" : "Выбрать шаблон"}
                </span>
                {(analyzing || currentTemplate?.status === "failed") && !uploading && (
                  <span className={styles.caption}>{currentTemplate?.status === "failed" ? "Анализ не удался · выберите другой" : "Анализируем оформление…"}</span>
                )}
              </span>
              <IconChevronDown size={16} className={styles.chevron} aria-hidden />
            </UnstyledButton>
          </Menu.Target>
          <Menu.Dropdown className={styles.dropdown} onFocus={onDropdownFocus} onKeyDown={onDropdownKeyDown}>
            <Menu.Item {...uploadProps} className={styles.upload} leftSection={<span className={styles.addIcon}><IconPlus size={21} /></span>} disabled={uploading} data-testid="template-upload">
              <Text size="sm" fw={600}>Добавить свой</Text>
              <Text size="xs" c="dimmed">Загрузить презентацию .pptx</Text>
            </Menu.Item>
            <Menu.Divider />
            <Menu.Label>Из библиотеки</Menu.Label>
            <div className={styles.list}>
            {templates.length === 0 && <Text size="xs" c="dimmed" px="sm" py="md">Пока нет шаблонов. Добавьте свой первым.</Text>}
            {templates.map((t) => (
              <Menu.Item
                key={t.template_id}
                className={styles.option}
                data-selected={t.template_id === project.template_id || undefined}
                onClick={() => onSelectTemplate(t.template_id)}
                leftSection={<span className={styles.optionPreview}>{thumb(t) ? <img src={thumb(t)} alt="" /> : <IconTemplate size={20} stroke={1.5} />}</span>}
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
            </div>
            <Menu.Divider />
            <Menu.Item component={Link} href={project.template_id ? `/templates?id=${encodeURIComponent(project.template_id)}` : "/templates"} leftSection={<IconTemplate size={14} />}>
              {project.template_id ? "Что извлечено из шаблона" : "Библиотека шаблонов"}
            </Menu.Item>
          </Menu.Dropdown>
        </Menu>
        )}
      </FileButton>
      {project.template_id && <Text size="xs" c="dimmed">Теперь опишите задачу презентации или добавьте материалы.</Text>}
    </Stack>
  );
}
