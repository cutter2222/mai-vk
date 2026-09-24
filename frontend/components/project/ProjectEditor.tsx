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
import { warmUpOffice, type OfficeEditHandle } from "@/components/office/OfficeEditor";

import { api, ApiError, type CapabilitiesResponse, type OfficeLogoAction, type OfficeSelection } from "@/lib/api/client";
import type { GenerationRequest } from "@/lib/api/types";
import { useGenerationSession } from "@/lib/hooks/useGenerationSession";
import { setPanelOpen, usePanelOpen } from "@/lib/state/panel";
import { plural, VARIANT_LABELS } from "@/lib/format";
import type { LiveSelection } from "@/lib/editor/officeLive";
import { isQuestion, slideRequest } from "@/lib/slideRequest";
import { addProjectFiles, appendMessage, getProject, patchProjectFile, slideCountAsked, updateProject, type Project } from "@/lib/state/projects";

import { ChatPanel } from "./chat/ChatPanel";
import { isDeckJob } from "./chat/feed";
import type { CardContext } from "./chat/cards";
import { useChat } from "./chat/useChat";
import { FilesPanel } from "./files/FilesPanel";
import { BriefFields } from "./panels/BriefFields";
import { SettingsPanel, settingsError } from "./panels/SettingsPanel";
import { PreviewPane } from "./preview/PreviewPane";
import { GenerationProgress } from "./preview/GenerationProgress";
import { ProjectHeader } from "./ProjectHeader";
import { ProjectOffice } from "./office/ProjectOffice";
import { TemplatePicker } from "./TemplatePicker";

/** Что показывает левая панель: разговор или загруженные файлы проекта. */
type Tab = "chat" | "files";

/** Проект: слева чат/файлы, справа редактор слайдов и сохранённое превью. */
export function ProjectEditor({ project }: { project: Project }) {
  const [tab, setTab] = useState<Tab>("chat");
  // Панель с чатом занимает 460 px. На правке слайда это место нужнее слайду, поэтому она
  // сворачивается в рельс с иконками: экран остаётся тем же, а холст становится больше.
  const panelOpen = usePanelOpen();
  const [caps, setCaps] = useState<CapabilitiesResponse | null>(null);
  const [officeEnabled, setOfficeEnabled] = useState(false);
  const [officeScript, setOfficeScript] = useState<string | null>(null);
  // Когда редактор открыл документ этого задания: слайды видны, открытие закончилось.
  const [editorReady, setEditorReady] = useState<{ jobId: string; at: string } | null>(null);
  const [officeOpened, setOfficeOpened] = useState(false);
  const officeEdit = useRef<OfficeEditHandle>(null);
  const [officeSelection, setOfficeSelection] = useState<OfficeSelection | null>(null);
  const selectOfficeObject = useCallback((value: OfficeSelection | null) => {
    setOfficeSelection(value);
    if (value) { setPanelOpen(true); setTab("chat"); }
  }, []);
  // Слайд и объект, выбранные в живом редакторе ONLYOFFICE: адресат сообщения в чате.
  const [liveSelection, setLiveSelection] = useState<LiveSelection | null>(null);
  // Крестик на плашке снимает адресацию до следующего выбора в редакторе.
  const [liveDismissed, setLiveDismissed] = useState<string | null>(null);
  const [officeActionsTarget, setOfficeActionsTarget] = useState<HTMLDivElement | null>(null);
  const [starting, setStarting] = useState(false);
  const [briefModal, setBriefModal] = useState(false);
  const [briefSnapshot, setBriefSnapshot] = useState("");
  const patch = useCallback((p: Partial<Project> | ((p: Project) => Partial<Project>)) => updateProject(project.project_id, p), [project.project_id]);

  useEffect(() => {
    api.capabilities().then(setCaps).catch(() => setCaps(null));
    api.office.capabilities().then((value) => {
      setOfficeEnabled(value.enabled);
      setOfficeScript(value.script_url ?? null);
    }).catch(() => setOfficeEnabled(false));
  }, []);

  const showPanel = (open: boolean, next?: Tab) => {
    if (next) setTab(next);
    setPanelOpen(open);
  };

  const session = useGenerationSession(project.job_id, (jobId) => patch({ job_id: jobId }), { loadAudit: false });

  const officeAvailable = officeEnabled && Boolean(session.jobId && session.variant?.artifacts?.pptx);
  if (officeAvailable && !officeOpened) setOfficeOpened(true);
  const officePresent = officeEnabled && officeOpened;
  const deckJob = isDeckJob(project, session.result);
  const jobRunning = starting || Boolean(project.job_id && !session.terminal);

  // Сборка читает последнее состояние проекта, а не снимок рендера: чат зовёт её сразу после
  // импорта содержания или выбора шаблона, когда новый рендер ещё не случился.
  const generate = useCallback(async (): Promise<boolean> => {
    const p = getProject(project.project_id) ?? project;
    if (!p.template_id || !p.package_id) return false;
    const error = settingsError(p.settings);
    if (error) {
      notifications.show({ color: "red", title: "Проверьте параметры", message: error });
      return false;
    }
    setStarting(true);
    const s = p.settings;
    const req: GenerationRequest = {
      schema_version: "1.2",
      template_id: p.template_id,
      package_id: p.package_id,
      idempotency_key: `ui-${p.project_id}-${Date.now().toString(36)}`,
      settings: {
        // Режим оформления не спрашиваем: по умолчанию смешанный, сменить можно фразой в чате.
        design_mode: s.design_mode ?? "mixed",
        ...(slideCountAsked(s) ? { slide_count: s.mode === "exact" ? { exact: s.exact } : { min: s.min, max: s.max } } : {}),
        language: p.brief.language || "ru",
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
  }, [project, patch]);

  const chat = useChat(project, session, generate);
  // Готовая презентация: ход открытия — строкой в чате под «Открываю…», а справа сразу место
  // редактора, без карточки по центру и без полосы сверху.
  const deckOpening = deckJob ? jobRunning : Boolean(chat.deckStart?.preparing);
  const editorSlot = officePresent || deckOpening;
  // Редактор прогревается, как только презентация готовится или уже есть: скрипты ONLYOFFICE
  // грузятся параллельно с подготовкой файла, а не после неё. «Открыть как презентацию» —
  // сразу по ответу, пока файл ещё едет на сервер.
  const deckAnswered = chat.staged.some((s) => s.answer === "deck");
  const needsEditor = Boolean(chat.deckStart || deckAnswered || starting || project.job_id);
  useEffect(() => {
    if (officeScript && needsEditor) warmUpOffice(officeScript);
  }, [officeScript, needsEditor]);
  // Готовая презентация открыта, когда её слайды видны: в редакторе — по его сигналу, без
  // редактора — когда файл опубликован. Секундомер — первого открытия в этой вкладке: повтор
  // разбора или проверка моделью создают новое задание, но презентация уже была открыта.
  const deckSince = chat.deckStart?.since;
  const [prevDeckSince, setPrevDeckSince] = useState(deckSince);
  if (prevDeckSince !== deckSince) {
    setPrevDeckSince(deckSince);
    setEditorReady(null);
  }
  const deckShownAt = !deckJob || !session.jobId ? null
    : officeEnabled ? (chat.deckStart ? editorReady?.at ?? null : editorReady?.jobId === session.jobId ? editorReady.at : null)
      : (session.variant?.ready_at ?? null);
  const onEditorReady = () => {
    const jobId = session.jobId;
    if (jobId) setEditorReady((old) => (old && (old.jobId === jobId || chat.deckStart) ? old : { jobId, at: new Date().toISOString() }));
  };
  const pendingSlides = session.variant?.slide_count ?? 0;

  // Адресат из живого редактора: выделенный объект (одна фигура с именем) или слайд. Правка
  // слайда пересобирает его моделью по плану варианта, поэтому плашка слайда — только у
  // собранной презентации; объект правится в самой копии.
  const liveKey = liveSelection ? JSON.stringify(liveSelection) : null;
  const liveObject = liveSelection?.objects.length === 1 && liveSelection.objects[0].name ? liveSelection.objects[0] : null;
  const editVariant = session.variant;
  const slideEditable = Boolean(session.jobId && editVariant?.artifacts?.pptx && editVariant.status !== "failed");
  const liveTarget = officePresent && liveSelection && liveKey !== liveDismissed && !officeSelection && (liveObject || slideEditable)
    ? liveObject
      ? { kind: "object" as const, slide: liveSelection.slide, label: liveObject.label ? `«${liveObject.label.length > 48 ? `${liveObject.label.slice(0, 47)}…` : liveObject.label}»` : OBJECT_KINDS[liveObject.kind] }
      : { kind: "slide" as const, slide: liveSelection.slide, label: VARIANT_LABELS[editVariant?.variant_id ?? ""] ?? editVariant?.variant_id ?? "" }
    : null;

  // Человек написал в чат — панель возвращается к разговору: из «Файлов» его же сообщение и
  // ответ на него были бы не видны.
  const send: typeof chat.send = async (text, files, target) => {
    setTab("chat");
    const logo = officePresent ? logoRequest(text) : null;
    if (logo) {
      await changeLogo(logo, text, files);
      return;
    }
    // Выделенный в редакторе объект: правка только его, в самой копии (сервер находит фигуру
    // по имени и положению после сохранения).
    if (liveTarget?.kind === "object" && liveObject && liveSelection && !files.length && !/^\/edit\s+/i.test(text.trim())) {
      appendMessage(project.project_id, { role: "user", kind: "message", text: `Слайд ${liveTarget.slide} · ${liveTarget.label}\n${text}`, file_ids: [] });
      try {
        if (!officeEdit.current) throw new Error("Дождитесь открытия презентации.");
        const message = await officeEdit.current.edit(text.trim(), { slide: liveSelection.slide, name: liveObject.name, ...(liveObject.box ? { box: liveObject.box } : {}), ...(liveObject.inGroup ? { in_group: true } : {}) });
        appendMessage(project.project_id, { role: "assistant", kind: "text", text: message });
      } catch (e) {
        appendMessage(project.project_id, { role: "assistant", kind: "text", text: e instanceof Error ? e.message : "Правка не применена." });
      }
      return;
    }
    // Номер слайда — из плашки выбранного в редакторе слайда или из фразы («на слайде 3 …»);
    // правка идёт в задание ревизии, и пересобранный слайд встаёт в открытую копию. Пока
    // презентация разбирается в фоне, сервер ставит правку за разбором и говорит, когда применит.
    const phrase = officePresent && !officeSelection && !target && !files.length && !/^\/edit\s+/i.test(text.trim()) ? slideRequest(text) : null;
    // Вопрос («что на этом слайде?») при плашке слайда — к ассистенту: он видит содержимое.
    const slide = phrase ?? (liveTarget?.kind === "slide" && !target && !files.length && !isQuestion(text) ? liveTarget.slide : null);
    const variant = session.variant;
    if (slide && session.jobId && variant?.artifacts?.pptx && variant.status !== "failed") {
      const count = variant.slide_count ?? 0;
      if (count && slide > count) {
        appendMessage(project.project_id, { role: "user", kind: "message", text, file_ids: [] });
        appendMessage(project.project_id, { role: "assistant", kind: "text", text: `В презентации ${count} ${plural(count, "слайд", "слайда", "слайдов")}, слайда ${slide} нет.` });
        return;
      }
      // Слайды в редакторе добавлены или удалены руками: номер в ленте не совпадает с планом.
      if (!phrase && liveSelection && count && liveSelection.count !== count) {
        appendMessage(project.project_id, { role: "user", kind: "message", text, file_ids: [] });
        appendMessage(project.project_id, { role: "assistant", kind: "text", text: "Слайды в редакторе добавлены или удалены вручную, поэтому пересобрать слайд из чата не получится. Выделите объект на слайде и напишите, что с ним сделать." });
        return;
      }
      await chat.send(text, files, { jobId: session.jobId, variantId: variant.variant_id, revision: variant.revision, slideIndex: slide - 1 });
      return;
    }
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

  // Знак шаблона лежит на макетах, на слайде его не выделить: «замени логотип» с картинкой
  // или «убери логотип» меняет его на всех слайдах одной правкой документа.
  const changeLogo = async (action: OfficeLogoAction["action"], text: string, files: File[]) => {
    const pid = project.project_id;
    const image = files.find((f) => /\.(png|jpe?g|gif|bmp)$/i.test(f.name));
    let fileId: string | undefined;
    let uploadError = "";
    if (action === "replace" && image) {
      try {
        const [row] = await addProjectFiles(pid, [image]);
        fileId = row?.file_id;
        // Логотип — не материал: в содержание презентации картинка не идёт.
        if (fileId) void patchProjectFile(pid, fileId, { kind: "other" });
      } catch (e) {
        uploadError = e instanceof Error ? e.message : "картинка не загрузилась";
      }
    }
    appendMessage(pid, { role: "user", kind: "message", text, file_ids: fileId ? [fileId] : [] });
    try {
      if (uploadError) throw new Error(`Не удалось загрузить логотип: ${uploadError}.`);
      if (action === "replace" && !fileId) throw new Error("Прикрепите картинку нового логотипа (PNG или JPG) — заменю его на всех слайдах сразу.");
      if (!officeEdit.current) throw new Error("Дождитесь открытия презентации.");
      const message = await officeEdit.current.edit(text.trim(), undefined, { action, ...(fileId ? { file_id: fileId } : {}) });
      appendMessage(pid, { role: "assistant", kind: "text", text: message });
    } catch (e) {
      appendMessage(pid, { role: "assistant", kind: "text", text: e instanceof Error ? e.message : "Логотип не изменён." });
    }
  };

  // Вопрос о брошенной презентации задаётся в ленте чата: из «Файлов» панель возвращается к
  // разговору, иначе вопрос висит невидимым, пока файл грузится.
  const attach = (files: File[]): File[] => {
    const rest = chat.attach(files);
    if (rest.length < files.length) setTab("chat");
    return rest;
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
    deckJob,
    deckStart: chat.deckStart,
    deckShownAt,
    onRecheckDeck: () => void chat.recheckDeck(),
  };

  // Две вкладки: разговор целиком и загруженные файлы. Шаги работы — реплики той же ленты.
  const TABS: Array<{ key: Tab; label: string; icon: React.ReactNode; badge?: number }> = [
    { key: "chat", label: "Чат", icon: <IconMessage size={16} stroke={1.7} /> },
    { key: "files", label: "Файлы", icon: <IconFolder size={16} stroke={1.7} />, badge: project.files.length || undefined },
  ];

  return (
    <div className="editor" data-testid="project-editor">
      <ProjectHeader
        project={project}
        session={session}
        onTitle={(title) => patch({ title })}
        officeActionsRef={setOfficeActionsTarget}
        templatePicker={<TemplatePicker project={project} onSelectTemplate={chat.selectTemplate} onUploadTemplate={chat.addTemplate} />}
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
            <div className="editor-panel-tabs" role="tablist" aria-label="Разделы панели проекта">
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
              <ChatPanel ctx={ctx} onSend={send} officeSelection={officeSelection} onDismissOfficeSelection={() => setOfficeSelection(null)} liveTarget={liveTarget} onDismissLiveTarget={() => setLiveDismissed(liveKey)} suggestions={chat.suggestions} onAttach={attach} staged={chat.staged} onAnswerStaged={chat.answerStaged} speech={Boolean(caps?.features.speech)} />
            ) : (
              <FilesPanel project={project} onAdd={(files) => { const rest = attach(files); if (rest.length) void chat.send("", rest); }} onRemove={(fid) => void chat.removeFile(fid)} onSelectTemplate={chat.selectTemplate} />
            )}
          </div>
        </aside>

        <section className="editor-preview" data-testid="preview-pane">
          {editorSlot && <div className="office-slot">
            {jobRunning && !deckJob && <GenerationProgress session={session} compact starting={starting} />}
            {officePresent
              ? <ProjectOffice session={session} title={project.title} projectId={project.project_id} editRef={officeEdit} actionsTarget={officeActionsTarget} selection={officeSelection} onSelectionChange={selectOfficeObject} onEditorReady={onEditorReady} onLiveSelection={setLiveSelection} />
              : <div className="office-pending" data-testid="office-pending">
                {/* Место под каждый слайд, пока файл готовится: число страниц сервер знает сразу. */}
                {pendingSlides > 0 && <div className="slide-skeletons" data-testid="slide-skeletons" aria-hidden>
                  {Array.from({ length: Math.min(pendingSlides, 80) }, (_, i) => (
                    <div key={i} className="slide-skeleton" style={{ animationDelay: `${(i % 24) * 90}ms` }}><span>{i + 1}</span></div>
                  ))}
                </div>}
                <Text size="sm" c="dimmed">{pendingSlides > 0 ? `Готовлю ${pendingSlides} ${plural(pendingSlides, "слайд", "слайда", "слайдов")} к показу…` : "Слайды появятся здесь, как только презентация откроется."}</Text>
              </div>}
          </div>}
          {!editorSlot && <PreviewPane
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

/** Просьба про знак шаблона: «замени логотип на наш», «убери лого». Остальное — обычный разговор. */
function logoRequest(text: string): OfficeLogoAction["action"] | null {
  const t = text.toLowerCase();
  // \b в JavaScript не видит границ кириллических слов: «лого», за которым не идёт буква.
  if (!/(логотип|лого(?![а-яё])|logo)/.test(t)) return null;
  if (/(убер|убра|удал|сним|скрой|спрячь|без\s+логотип)/.test(t)) return "remove";
  if (/(замен|помен|постав|вставь|обнови|сделай|наш|свой|друг)/.test(t)) return "replace";
  return null;
}

/** Подпись выделенного объекта без текста. */
const OBJECT_KINDS: Record<LiveSelection["objects"][number]["kind"], string> = {
  shape: "фигура",
  image: "картинка",
  chart: "диаграмма",
  table: "таблица",
};
