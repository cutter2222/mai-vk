"use client";

import { ActionIcon, Button, Group, Modal, Stack, Tabs, Text, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconFolder,
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarLeftExpand,
  IconMessage,
  IconX,
} from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";

import { api, ApiError, type CapabilitiesResponse, type ContentDetail, type TemplateDetail } from "@/lib/api/client";
import type { GenerationRequest } from "@/lib/api/types";
import { usePolling } from "@/lib/api/usePolling";
import { useGenerationSession } from "@/lib/hooks/useGenerationSession";
import { useSlideEditor } from "@/lib/hooks/useSlideEditor";
import { setPanelOpen, usePanelOpen } from "@/lib/state/panel";
import { appendMessage, updateProject, type Project } from "@/lib/state/projects";

import { ChatPanel } from "./chat/ChatPanel";
import type { CardContext } from "./chat/cards";
import { useChat } from "./chat/useChat";
import { FilesPanel } from "./files/FilesPanel";
import { BriefFields } from "./panels/BriefFields";
import { SettingsPanel, settingsError } from "./panels/SettingsPanel";
import { PreviewPane } from "./preview/PreviewPane";
import { ProjectHeader } from "./ProjectHeader";

type Tab = "chat" | "files";

/** Редактор проекта: слева чат (или файлы проекта), справа предпросмотр слайдов. */
export function ProjectEditor({ project }: { project: Project }) {
  const [tab, setTab] = useState<Tab>("chat");
  // Панель с чатом занимает 460 px. На правке слайда это место нужнее слайду, поэтому она
  // сворачивается в рельс с иконками: экран остаётся тем же, а холст становится больше.
  const panelOpen = usePanelOpen();
  const [caps, setCaps] = useState<CapabilitiesResponse | null>(null);
  const [starting, setStarting] = useState(false);
  const [briefModal, setBriefModal] = useState(false);
  const [briefSnapshot, setBriefSnapshot] = useState("");
  const patch = useCallback((p: Partial<Project> | ((p: Project) => Partial<Project>)) => updateProject(project.project_id, p), [project.project_id]);

  useEffect(() => {
    api.capabilities().then(setCaps).catch(() => setCaps(null));
  }, []);

  const showPanel = (open: boolean, next?: Tab) => {
    if (next) setTab(next);
    setPanelOpen(open);
  };

  const template = usePolling<TemplateDetail>(
    project.template_id ? () => api.templates.get(project.template_id as string) : null,
    (d) => d.status === "succeeded" || d.status === "failed",
    [project.template_id],
  );

  const session = useGenerationSession(project.job_id, (jobId) => patch({ job_id: jobId }));

  const pkg = usePolling<ContentDetail>(
    project.package_id ? () => api.content.get(project.package_id as string) : null,
    (d) => d.status === "succeeded" || d.status === "failed",
    [project.package_id],
  );

  // Визуальный редактор слайдов: черновик правок и применение одной ревизией; карточка хода — в чат.
  const onPatchStarted = useCallback(
    (patchJobId: string, slideIndex: number) => {
      const jobId = project.job_id;
      const variantId = session.variant?.variant_id;
      if (!jobId || !variantId) return;
      appendMessage(project.project_id, { role: "assistant", kind: "edit_card", job_id: jobId, variant_id: variantId, edit_job_id: patchJobId, slide_index: slideIndex });
    },
    [project.job_id, project.project_id, session.variant?.variant_id],
  );
  const editor = useSlideEditor(session, { profile: template.data?.profile, pkg: pkg.data?.package, onPatchStarted });

  const generate = useCallback(async (): Promise<boolean> => {
    if (!project.template_id || !project.package_id) return false;
    const error = settingsError(project.settings);
    if (error) {
      notifications.show({ color: "red", title: "Проверьте параметры", message: error });
      return false;
    }
    setStarting(true);
    const s = project.settings;
    const req: GenerationRequest = {
      schema_version: "1.2",
      template_id: project.template_id,
      package_id: project.package_id,
      idempotency_key: `ui-${project.project_id}-${Date.now().toString(36)}`,
      settings: {
        slide_count: s.mode === "exact" ? { exact: s.exact } : { min: s.min, max: s.max },
        language: project.brief.language || "ru",
        variants: s.variants as NonNullable<GenerationRequest["settings"]>["variants"],
        generate_images: s.images,
        run_contextual_audit: s.contextual,
        force_regenerate: s.force,
        ...(s.seed !== null ? { seed: s.seed } : {}),
      },
    };
    try {
      const res = await api.generations.create(req);
      patch({ job_id: res.job_id, chosen_variant: null });
      return true;
    } catch (e) {
      notifications.show({ color: "red", title: "Генерация не запущена", message: e instanceof ApiError ? e.message : "Неизвестная ошибка", icon: <IconX size={16} /> });
      return false;
    } finally {
      setStarting(false);
    }
  }, [project.template_id, project.package_id, project.settings, project.brief.language, project.project_id, patch]);

  const chat = useChat(project, session, generate);

  const openAudit = (variantId?: string) => {
    if (variantId) session.setSelectedVariant(variantId);
    session.setLayout("single");
    session.setAuditOpen(true);
  };

  const repairAll = () => {
    const report = session.audit.data;
    if (!report) {
      openAudit();
      return;
    }
    const ids = report.issues.filter((i) => i.fix.available).map((i) => i.issue_id);
    if (ids.length === 0) {
      notifications.show({ color: "gray", title: "Нечего исправлять автоматически", message: "У найденных проблем нет автоматического исправления." });
      return;
    }
    void session.repair(ids);
  };

  // Бриф — часть контент-пакета: после правок он переимпортируется, чтобы генерация видела новые поля.
  const closeBrief = () => {
    setBriefModal(false);
    if (JSON.stringify(project.brief) !== briefSnapshot && project.brief.title.trim()) void chat.importMaterials();
  };

  const ctx: CardContext = {
    project,
    session,
    onResolveTemplate: (messageId, fileId, answer) => void chat.resolveTemplateQuestion(messageId, fileId, answer),
    onEditBrief: () => {
      setBriefSnapshot(JSON.stringify(project.brief));
      setBriefModal(true);
    },
    onSetPurpose: (purpose) => void chat.setPurpose(purpose),
    onGenerate: () => void generate(),
    generating: starting,
    onOpenAudit: openAudit,
    onRepairAll: repairAll,
    onRetryImport: () => void chat.importMaterials(),
  };

  const TABS: Array<{ key: Tab; label: string; icon: React.ReactNode; badge?: number }> = [
    { key: "chat", label: "Чат", icon: <IconMessage size={16} stroke={1.7} /> },
    { key: "files", label: "Файлы", icon: <IconFolder size={16} stroke={1.7} />, badge: project.files.length },
  ];

  return (
    <div className="editor" data-testid="project-editor">
      <ProjectHeader
        project={project}
        session={session}
        onTitle={(title) => patch({ title })}
        onSelectTemplate={chat.selectTemplate}
        onUploadTemplate={(file) => void chat.addTemplate(file)}
      />
      <div className="editor-body">
        {/* Свёрнутая панель оставляет рельс: развернуть и сразу открыть нужный раздел. */}
        {!panelOpen && (
          <div className="editor-rail" data-testid="panel-rail">
            <Tooltip label="Развернуть панель" position="right">
              <ActionIcon variant="subtle" color="gray" size="lg" onClick={() => showPanel(true)} aria-label="Развернуть панель" data-testid="panel-expand">
                <IconLayoutSidebarLeftExpand size={18} stroke={1.7} />
              </ActionIcon>
            </Tooltip>
            {TABS.map((t) => (
              <Tooltip key={t.key} label={t.label} position="right">
                <ActionIcon variant="subtle" color="gray" size="lg" onClick={() => showPanel(true, t.key)} aria-label={t.label} data-testid={`rail-${t.key}`}>
                  {t.icon}
                </ActionIcon>
              </Tooltip>
            ))}
          </div>
        )}
        {/* Разделы панели переехали с вертикального рельса в её же шапку: рельс занимал
            колонку ради двух кнопок и добавлял четвёртый слой хрома на экран. */}
        <aside className="editor-panel" data-open={panelOpen} aria-hidden={!panelOpen} inert={!panelOpen}>
          <div className="editor-panel-inner">
            <div className="editor-panel-tabs" role="tablist" aria-label="Разделы проекта">
              {TABS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  role="tab"
                  className="panel-tab"
                  data-active={tab === t.key || undefined}
                  aria-selected={tab === t.key}
                  onClick={() => setTab(t.key)}
                  data-testid={`tab-${t.key}`}
                >
                  {t.icon}
                  <span>{t.label}</span>
                  {t.badge ? <b>{t.badge}</b> : null}
                </button>
              ))}
              <Tooltip label="Свернуть панель">
                <ActionIcon className="panel-collapse" variant="subtle" color="gray" size="md" onClick={() => showPanel(false)} aria-label="Свернуть панель" data-testid="panel-collapse">
                  <IconLayoutSidebarLeftCollapse size={18} stroke={1.7} />
                </ActionIcon>
              </Tooltip>
            </div>
            {tab === "chat" ? (
              <ChatPanel ctx={ctx} onSend={chat.send} onAttach={chat.attach} staged={chat.staged} onAnswerStaged={chat.answerStaged} />
            ) : (
              <FilesPanel project={project} session={session} onAdd={(files) => { const rest = chat.attach(files); if (rest.length) void chat.send("", rest); }} onRemove={(fid) => void chat.removeFile(fid)} onSelectTemplate={chat.selectTemplate} />
            )}
          </div>
        </aside>

        <section className="editor-preview" data-testid="preview-pane">
          <PreviewPane
            project={project}
            session={session}
            editor={editor}
            pkg={pkg.data?.package}
            templateDetail={template.data}
            templateError={template.error}
            onChoose={(variantId) => patch({ chosen_variant: variantId })}
          />
        </section>
      </div>

      <Modal opened={briefModal} onClose={closeBrief} title="Задача и параметры" size="lg" centered>
        <Tabs defaultValue="brief">
          <Tabs.List mb="md">
            <Tabs.Tab value="brief">Бриф</Tabs.Tab>
            <Tabs.Tab value="settings">Параметры генерации</Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="brief">
            <Stack gap="md">
              <BriefFields brief={project.brief} onChange={(brief) => patch({ brief })} />
              <Text size="xs" c="dimmed">Бриф уходит в содержание вместе с материалами: после правок они переимпортируются при следующей генерации.</Text>
            </Stack>
          </Tabs.Panel>
          <Tabs.Panel value="settings">
            <SettingsPanel settings={project.settings} onChange={(settings) => patch({ settings })} caps={caps} />
          </Tabs.Panel>
        </Tabs>
        <Group justify="flex-end" mt="lg">
          <Button onClick={closeBrief} data-testid="brief-modal-done">Готово</Button>
        </Group>
      </Modal>
    </div>
  );
}
