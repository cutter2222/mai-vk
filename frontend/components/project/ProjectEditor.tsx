"use client";

import { ActionIcon, Button, Group, Indicator, Modal, Stack, Tabs, Text, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconFolder, IconMessage, IconX } from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";

import { api, ApiError, type CapabilitiesResponse, type TemplateDetail } from "@/lib/api/client";
import type { GenerationRequest } from "@/lib/api/types";
import { usePolling } from "@/lib/api/usePolling";
import { useGenerationSession } from "@/lib/hooks/useGenerationSession";
import { updateProject, type Project } from "@/lib/state/projects";

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
  const [caps, setCaps] = useState<CapabilitiesResponse | null>(null);
  const [starting, setStarting] = useState(false);
  const [briefModal, setBriefModal] = useState(false);
  const [briefSnapshot, setBriefSnapshot] = useState("");
  const patch = useCallback((p: Partial<Project> | ((p: Project) => Partial<Project>)) => updateProject(project.project_id, p), [project.project_id]);

  useEffect(() => {
    api.capabilities().then(setCaps).catch(() => setCaps(null));
  }, []);

  const template = usePolling<TemplateDetail>(
    project.template_id ? () => api.templates.get(project.template_id as string) : null,
    (d) => d.status === "succeeded" || d.status === "failed",
    [project.template_id],
  );

  const session = useGenerationSession(project.job_id, (jobId) => patch({ job_id: jobId }));

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
    { key: "chat", label: "Чат", icon: <IconMessage size={20} stroke={1.6} /> },
    { key: "files", label: "Файлы проекта", icon: <IconFolder size={20} stroke={1.6} />, badge: project.files.length },
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
        <nav className="editor-rail" aria-label="Разделы проекта">
          {TABS.map((t) => {
            const button = (
              <ActionIcon
                variant={tab === t.key ? "light" : "subtle"}
                color="gray"
                size="lg"
                style={tab === t.key ? { color: "var(--mantine-color-graphite-8)" } : undefined}
                onClick={() => setTab(t.key)}
                aria-label={t.label}
                aria-current={tab === t.key ? "page" : undefined}
                data-testid={`tab-${t.key}`}
              >
                {t.icon}
              </ActionIcon>
            );
            return (
              <Tooltip key={t.key} label={t.label} position="right">
                {t.badge ? <Indicator label={t.badge} size={16} color="graphite" offset={4}>{button}</Indicator> : button}
              </Tooltip>
            );
          })}
        </nav>

        <aside className="editor-panel">
          {tab === "chat" ? (
            <ChatPanel ctx={ctx} onSend={chat.send} onAttach={chat.attach} staged={chat.staged} onAnswerStaged={chat.answerStaged} />
          ) : (
            <FilesPanel project={project} session={session} onAdd={(files) => { const rest = chat.attach(files); if (rest.length) void chat.send("", rest); }} onRemove={(fid) => void chat.removeFile(fid)} onSelectTemplate={chat.selectTemplate} />
          )}
        </aside>

        <section className="editor-preview" data-testid="preview-pane">
          <PreviewPane
            project={project}
            session={session}
            templateDetail={template.data}
            templateError={template.error}
            onChoose={(variantId) => patch({ chosen_variant: variantId })}
            onOpenTemplateTab={() => setTab("chat")}
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
