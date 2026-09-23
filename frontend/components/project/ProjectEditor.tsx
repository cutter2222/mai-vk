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
import { useCallback, useEffect, useRef, useState } from "react";
import type { OfficeEditHandle } from "@/components/office/OfficeEditor";

import { api, ApiError, type CapabilitiesResponse, type OfficeSelection } from "@/lib/api/client";
import type { GenerationRequest } from "@/lib/api/types";
import { useGenerationSession } from "@/lib/hooks/useGenerationSession";
import { setPanelOpen, usePanelOpen } from "@/lib/state/panel";
import { appendMessage, updateProject, type Project } from "@/lib/state/projects";

import { ChatPanel } from "./chat/ChatPanel";
import { countTags, isVisibleProjectMessage, TAG_LABELS, TAG_ORDER, type ChatTag } from "./chat/tags";
import type { CardContext } from "./chat/cards";
import { useChat } from "./chat/useChat";
import { FilesPanel } from "./files/FilesPanel";
import { BriefFields } from "./panels/BriefFields";
import { SettingsPanel, settingsError } from "./panels/SettingsPanel";
import { PreviewPane } from "./preview/PreviewPane";
import { GenerationProgress } from "./preview/GenerationProgress";
import { ProjectHeader } from "./ProjectHeader";
import { ProjectOffice } from "./office/ProjectOffice";

/** Что показывает левая панель: всю ленту, ленту под меткой или файлы проекта. */
type Tab = "all" | ChatTag | "files";

/** Проект: слева чат/файлы, справа редактор слайдов и сохранённое превью. */
export function ProjectEditor({ project }: { project: Project }) {
  const [tab, setTab] = useState<Tab>("all");
  // Панель с чатом занимает 460 px. На правке слайда это место нужнее слайду, поэтому она
  // сворачивается в рельс с иконками: экран остаётся тем же, а холст становится больше.
  const panelOpen = usePanelOpen();
  const [caps, setCaps] = useState<CapabilitiesResponse | null>(null);
  const [officeEnabled, setOfficeEnabled] = useState(false);
  const [officeOpened, setOfficeOpened] = useState(false);
  const officeEdit = useRef<OfficeEditHandle>(null);
  const [officeSelection, setOfficeSelection] = useState<OfficeSelection | null>(null);
  const selectOfficeObject = useCallback((value: OfficeSelection | null) => {
    setOfficeSelection(value);
    if (value) { setPanelOpen(true); setTab("all"); }
  }, []);
  const [officeActionsTarget, setOfficeActionsTarget] = useState<HTMLDivElement | null>(null);
  const [starting, setStarting] = useState(false);
  const [briefModal, setBriefModal] = useState(false);
  const [briefSnapshot, setBriefSnapshot] = useState("");
  const patch = useCallback((p: Partial<Project> | ((p: Project) => Partial<Project>)) => updateProject(project.project_id, p), [project.project_id]);

  useEffect(() => {
    api.capabilities().then(setCaps).catch(() => setCaps(null));
    api.office.capabilities().then((value) => setOfficeEnabled(value.enabled)).catch(() => setOfficeEnabled(false));
  }, []);

  const showPanel = (open: boolean, next?: Tab) => {
    if (next) setTab(next);
    setPanelOpen(open);
  };

  const session = useGenerationSession(project.job_id, (jobId) => patch({ job_id: jobId }), { loadAudit: false });

  const officeAvailable = officeEnabled && Boolean(session.jobId && session.variant?.artifacts?.pptx);
  if (officeAvailable && !officeOpened) setOfficeOpened(true);
  const officePresent = officeEnabled && officeOpened;

  const generate = useCallback(async (): Promise<boolean> => {
    if (!project.template_id || !project.package_id) return false;
    if (!project.settings.design_mode && !project.job_id) {
      notifications.show({ title: "Выберите режим оформления", message: "Ответьте в чате: «По шаблону», «Смешанный» или «Все слайды новые»." });
      return false;
    }
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
        design_mode: s.design_mode ?? "mixed",
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
  }, [project.template_id, project.package_id, project.settings, project.brief.language, project.project_id, project.job_id, patch]);

  const chat = useChat(project, session, generate);

  // Человек написал в чат — лента возвращается к разговору. Под открытой меткой его же
  // сообщение и ответ на него были бы не видны: «написал и ничего не произошло».
  const send: typeof chat.send = async (text, files, target) => {
    setTab("all");
    if (officePresent && (officeSelection || /^\/edit\s+/i.test(text.trim()))) {
      const label = officeSelection ? `Слайд ${officeSelection.slide} · ${officeSelection.label} · v${officeSelection.revision}\n` : "";
      appendMessage(project.project_id, { role: "user", kind: "message", text: label + text, file_ids: [] });
      try {
        if (files.length) throw new Error("Прикрепите материалы отдельно. Здесь доступны текстовые правки и перемещение выбранного объекта; замена изображений и структуры — в ONLYOFFICE.");
        if (!officeEdit.current) throw new Error("Дождитесь открытия презентации.");
        const message = await officeEdit.current.edit(text.trim().replace(/^\/edit\s+/i, ""), officeSelection ?? undefined);
        appendMessage(project.project_id, { role: "assistant", kind: "text", text: message });
      } catch (e) {
        appendMessage(project.project_id, { role: "assistant", kind: "text", text: e instanceof Error ? e.message : "Правка не применена." });
      }
      return;
    }
    await chat.send(text, files, target);
  };

  // Бриф — часть контент-пакета: после правок он переимпортируется, чтобы генерация видела новые поля.
  const closeBrief = () => {
    setBriefModal(false);
    if (JSON.stringify(project.brief) !== briefSnapshot && project.brief.title.trim()) void chat.importMaterials();
  };

  const ctx: CardContext = {
    project,
    // The ONLYOFFICE slide selection is not the AI model's slide index.
    session: { ...session, slideTarget: null },
    onResolveTemplate: (messageId, fileId, answer) => void chat.resolveTemplateQuestion(messageId, fileId, answer),
    onEditBrief: () => {
      setBriefSnapshot(JSON.stringify(project.brief));
      setBriefModal(true);
    },
    onSetPurpose: (purpose) => void chat.setPurpose(purpose),
    onGenerate: () => void generate(),
    generating: starting,
    onRetryImport: () => void chat.importMaterials(),
  };

  // Ряд меток вместо вкладок: лента одна, а метка сужает её до шага работы. Пустые метки в
  // ряд не попадают — иначе он сам становится тем шумом, от которого избавляет.
  const events = project.events.filter((m) => isVisibleProjectMessage(m, session.jobId));
  const counts = countTags(events);
  const TABS: Array<{ key: Tab; label: string; icon?: React.ReactNode; badge?: number }> = [
    { key: "all", label: "Всё", icon: <IconMessage size={16} stroke={1.7} />, badge: events.length || undefined },
    ...TAG_ORDER.filter((tag) => counts[tag] > 0).map((tag) => ({ key: tag as Tab, label: TAG_LABELS[tag], badge: counts[tag] })),
    { key: "files", label: "Файлы", icon: <IconFolder size={16} stroke={1.7} />, badge: project.files.length || undefined },
  ];

  return (
    <div className="editor" data-testid="project-editor">
      <ProjectHeader
        project={project}
        session={session}
        onTitle={(title) => patch({ title })}
        officeActionsRef={setOfficeActionsTarget}
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
            {TABS.filter((t) => t.icon).map((t) => (
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
            <div className="editor-panel-tabs" role="tablist" aria-label="Метки ленты проекта">
              {TABS.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  role="tab"
                  className="panel-tab"
                  data-active={tab === t.key || undefined}
                  aria-selected={tab === t.key}
                  onClick={() => setTab(t.key)}
                  data-testid={t.key === "all" ? "tab-chat" : `tab-${t.key}`}
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
            {tab !== "files" ? (
              <ChatPanel ctx={ctx} onSend={send} officeSelection={officeSelection} onDismissOfficeSelection={() => setOfficeSelection(null)} suggestions={chat.suggestions} onAttach={chat.attach} staged={chat.staged} onAnswerStaged={chat.answerStaged} onSelectTemplate={chat.selectTemplate} onUploadTemplate={chat.addTemplate} filter={tab} onTag={(tag) => setTab(tag)} />
            ) : (
              <FilesPanel project={project} onAdd={(files) => { const rest = chat.attach(files); if (rest.length) void chat.send("", rest); }} onRemove={(fid) => void chat.removeFile(fid)} onSelectTemplate={chat.selectTemplate} />
            )}
          </div>
        </aside>

        <section className="editor-preview" data-testid="preview-pane">
          {officePresent && <div className="office-slot">
            {(starting || (project.job_id && !session.terminal)) && <GenerationProgress session={session} compact starting={starting} />}
            <ProjectOffice session={session} title={project.title} projectId={project.project_id} editRef={officeEdit} actionsTarget={officeActionsTarget} selection={officeSelection} onSelectionChange={selectOfficeObject} />
          </div>}
          {!officePresent && <PreviewPane
            project={project}
            session={session}
            officeEnabled={officeEnabled}
            starting={starting}
          />}
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
