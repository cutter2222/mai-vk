"use client";

import { notifications } from "@mantine/notifications";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api/client";
import type { GenerationSession, SlideTarget } from "@/lib/hooks/useGenerationSession";
import {
  addProjectFiles,
  askAssistant,
  appendMessage,
  getProject,
  patchMessage,
  patchProjectFile,
  refreshProject,
  removeProjectFile,
  updateProject,
  type BriefDraft,
  type PptxAnswer,
  type Project,
  type ProjectFile,
  type SettingsDraft,
} from "@/lib/state/projects";
import type { GenerationRequest } from "@/lib/api/types";
import { DESIGN_MODES, DESIGN_MODE_QUESTION, isDesignModeReply } from "@/lib/designMode";

const GENERATE_RE = /сгенерир|запусти|собер[иа]|сдела[йт]|сделаем|построй|начина|давай/i;
const EDIT_RE = /поменя[йть]|перестав|местами|удали|убери|добавь слайд|переимен/i;

const isPptx = (f: ProjectFile) => f.check.format === "pptx";
const isMaterial = (f: ProjectFile) => f.kind === "material";
const isPptxFile = (f: File) => /\.pptx$/i.test(f.name);

/** PPTX, брошенный в чат: вопрос «шаблон, готовая презентация или материал» показан сразу, файл ещё едет на сервер. */
export interface StagedPptx {
  local_id: string;
  name: string;
  size: number;
  answer?: PptxAnswer;
}

/**
 * Оркестратор чата. Файлы уходят на сервер в момент добавления и дальше передаются по идентификаторам;
 * тип распознаётся по расширению без модели; бриф из текста извлекает сервер;
 * каждый шаг конвейера появляется в чате карточкой, которая читает живое состояние проекта.
 */
export function useChat(project: Project, session: GenerationSession, generate: () => Promise<boolean>) {
  const id = project.project_id;
  const current = useCallback(() => getProject(id) as Project, [id]);

  const say = useCallback((text: string) => appendMessage(id, { role: "assistant", kind: "text", text }), [id]);

  const [staged, setStaged] = useState<StagedPptx[]>([]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const respond = useCallback(async (messageId: string) => {
    try {
      const response = await askAssistant(id, messageId);
      setSuggestions(response.options);
    } catch (e) {
      say(`Не удалось получить ответ: ${e instanceof Error ? e.message : "ошибка сервера"}`);
    }
  }, [id, say]);
  const stagedAnswers = useRef(new Map<string, PptxAnswer>());

  /** Импорт всех материалов проекта в новый пакет по file_ids. Возвращает package_id или null. */
  const importMaterials = useCallback(async (): Promise<string | null> => {
    const p = current();
    const materials = p.files.filter(isMaterial);
    const briefFilled = p.brief.title.trim().length > 0;
    if (materials.length === 0 && !briefFilled) return null;
    try {
      const res = await api.content.create(materials.map((f) => f.file_id), briefFilled ? { ...p.brief } : undefined);
      materials.forEach((f) => patchProjectFile(id, f.file_id, { package_id: res.package_id }));
      updateProject(id, { package_id: res.package_id });
      appendMessage(id, { role: "assistant", kind: "content_card", package_id: res.package_id, file_ids: materials.map((f) => f.file_id) });
      return res.package_id;
    } catch (e) {
      say(`Не удалось импортировать материалы: ${e instanceof ApiError ? e.message : "неизвестная ошибка"}.`);
      return null;
    }
  }, [id, say, current]);

  /** Файл проекта в библиотеку шаблонов с карточкой хода анализа в чате. */
  const uploadTemplate = useCallback(async (fileId: string, { quiet = false }: { quiet?: boolean } = {}) => {
    const meta = current().files.find((f) => f.file_id === fileId);
    if (!meta) return;
    try {
      const res = await api.templates.upload(fileId);
      patchProjectFile(id, fileId, { kind: "template", template_id: res.template_id });
      updateProject(id, { template_id: res.template_id });
      if (!quiet) appendMessage(id, { role: "assistant", kind: "template_card", template_id: res.template_id });
    } catch (e) {
      if (quiet) notifications.show({ color: "red", title: "Шаблон не загружен", message: e instanceof ApiError ? e.message : "Неизвестная ошибка" });
      else say(`Не удалось загрузить шаблон: ${e instanceof ApiError ? e.message : "неизвестная ошибка"}.`);
    }
  }, [id, say, current]);

  /** После шага проверяет, всё ли готово к генерации, и показывает карточку брифа с кнопкой. */
  const offerGeneration = useCallback((understood: string[] = [], briefSource?: "model" | "heuristic") => {
    const p = current();
    const last = p.events[p.events.length - 1];
    if (last && last.role === "assistant" && last.kind === "brief_card") return;
    appendMessage(id, { role: "assistant", kind: "brief_card", understood, missing_purpose: !p.brief.purpose, ...(briefSource ? { brief_source: briefSource } : {}) });
  }, [id, current]);

  /**
   * Материалы импортированы без задачи в тексте: без шаблона — просьба выбрать его, с шаблоном —
   * презентация собирается сразу (назначение можно уточнить в карточке задачи и пересобрать).
   */
  const afterMaterials = useCallback(async () => {
    const p = current();
    if (!p.package_id) return;
    if (!p.template_id) {
      say("Материалы в работе. Шаблон оформления не выбран: выберите его справа или перетащите PPTX и ответьте «Сделать шаблоном» — и я соберу презентацию.");
      return;
    }
    offerGeneration();
    if (!p.job_id && p.settings.design_mode) await generate();
  }, [current, say, offerGeneration, generate]);

  /** Сообщение, адресованное слайду: событие с адресом, запрос правки, карточка хода и результата. */
  const editSlide = useCallback(async (text: string, target: SlideTarget) => {
    const slideRef = { job_id: target.jobId, variant_id: target.variantId, revision: target.revision, slide_index: target.slideIndex };
    appendMessage(id, { role: "user", kind: "message", text, file_ids: [], slide_ref: slideRef });
    try {
      const editJobId = await session.requestEdit(target, text);
      appendMessage(id, { role: "assistant", kind: "edit_card", job_id: target.jobId, variant_id: target.variantId, edit_job_id: editJobId, slide_index: target.slideIndex });
    } catch (e) {
      const code = e instanceof ApiError ? e.code : "";
      if (code === "revision_stale") say("Ревизия слайда устарела: справа уже новая. Обновил результат — повторите просьбу к актуальному слайду.");
      else if (code === "repair_in_progress") say("Предыдущая правка этого варианта ещё применяется. Дождитесь её и повторите просьбу.");
      else if (code === "variant_failed") say("Этот вариант не собран, править в нём нечего. Выберите другой вариант.");
      else say(`Не удалось запустить правку: ${e instanceof ApiError ? e.message : "неизвестная ошибка"}.`);
    }
  }, [id, say, session]);

  const send = useCallback(async (text: string, files: File[], target: SlideTarget | null = null) => {
    const trimmed = text.trim();
    if (!trimmed && files.length === 0) return;
    setSuggestions([]);

    // 0. Сообщение к выбранному слайду — правка, а не бриф; вложения при этом идут обычным путём.
    const targeted = Boolean(trimmed && target);
    if (targeted && target) {
      await editSlide(trimmed, target);
      if (files.length === 0) return;
    }
    let rest = targeted ? "" : trimmed;

    // 1. Файлы попадают на сервер сразу: проверка и дедупликация там, в проекте остаются записи.
    let rows: ProjectFile[] = [];
    if (files.length) {
      try {
        rows = await addProjectFiles(id, files);
      } catch (e) {
        say(`Не удалось загрузить файлы: ${e instanceof ApiError ? e.message : "неизвестная ошибка"}.`);
        if (!rest) return;
      }
    }
    const message = appendMessage(id, { role: "user", kind: "message", text: rest, file_ids: rows.map((r) => r.file_id) });
    if (isDesignModeReply(rest)) {
      await respond(message.event_id);
      await refreshProject(id);
      if (!files.length) return;
      rest = ""; // Выбор режима не становится темой брифа при отправке с вложениями.
    }
    // A conversation about an existing deck must not become a new brief or an office edit.
    if (rest && !files.length && current().job_id) {
      await respond(message.event_id);
      return;
    }

    // 2. PPTX может быть и шаблоном, и материалом: спрашиваем.
    rows.filter((r) => isPptx(r) && r.kind !== "template").forEach((r) => appendMessage(id, { role: "assistant", kind: "template_question", file_id: r.file_id }));

    // 3. Неподдерживаемые типы остаются в файлах проекта.
    const others = rows.filter((r) => !isPptx(r) && r.kind === "other");
    if (others.length) {
      say(`${others.map((o) => `«${o.name}»`).join(", ")}: такой тип файла сохранён в файлах проекта, но при генерации пока не используется. Поддерживаются docx, xlsx, csv, pdf, md, txt и изображения.`);
    }

    // 4. Материалы импортируются пакетом вместе с уже загруженными; без текста задачи
    //    презентация собирается сразу, если выбран шаблон, иначе чат просит его выбрать.
    let imported = false;
    if (rows.some(isMaterial)) {
      imported = Boolean(await importMaterials());
      if (imported && !rest) {
        await afterMaterials();
        return;
      }
    }

    // 5. Текст: бриф извлекает сервер; поля, которых нет в сообщении, не трогаем.
    let understood: string[] = [];
    let briefSource: "model" | "heuristic" | undefined;
    // Намерение собрать презентацию: сервер (модель или правила) либо явная команда в тексте.
    let wantsGeneration = Boolean(rest) && GENERATE_RE.test(rest);
    if (rest) {
      if (EDIT_RE.test(rest) && current().job_id) {
        say("Чтобы изменить слайд, выберите его в ленте справа: в поле ввода появится метка слайда, и просьба применится к нему новой ревизией. Перестановка и удаление слайдов пока недоступны.");
      }
      try {
        const res = await api.brief.extract(rest, { ...current().brief });
        understood = res.understood;
        briefSource = res.source;
        if (res.intent === "generate") wantsGeneration = true;
        if (understood.length) {
          updateProject(id, (p) => {
            const brief: BriefDraft = { ...p.brief, ...res.brief } as BriefDraft;
            const settings: SettingsDraft = { ...p.settings };
            if (res.slide_count?.exact) Object.assign(settings, { mode: "exact", exact: res.slide_count.exact });
            else if (res.slide_count?.min && res.slide_count.max) Object.assign(settings, { mode: "range", min: res.slide_count.min, max: res.slide_count.max });
            if (res.variants?.length) settings.variants = res.variants;
            const title = res.brief.title && (p.title === "Новая презентация" || p.title === p.brief.title) ? res.brief.title : p.title;
            return { brief, settings, title };
          });
          // Бриф — часть контент-пакета: если материалы уже импортированы без него, переимпортируем.
          if (!imported && current().package_id && (res.brief.title || res.brief.purpose || res.brief.goal)) {
            imported = Boolean(await importMaterials());
          }
        }
      } catch {
        /* сервер не ответил: бриф можно заполнить вручную через «Изменить» */
      }
      if (!understood.length && !rows.length && !EDIT_RE.test(rest)) {
        await respond(message.event_id);
        if (!wantsGeneration) return;
      }
    }

    // 6. Бриф без файлов тоже содержание: пакет из одного брифа.
    const p = current();
    if (!p.package_id && p.brief.title.trim() && !imported) {
      imported = Boolean(await importMaterials());
    }

    // 7. Карточка брифа с кнопкой, когда есть что показать; просьба собрать запускает генерацию.
    if (understood.length || wantsGeneration || (current().template_id && current().package_id)) {
      offerGeneration(understood, understood.length ? briefSource : undefined);
    }
    const ready = current().template_id && current().package_id && current().brief.purpose;
    if (wantsGeneration && ready && current().settings.design_mode && !current().job_id) {
      await generate();
    }
  }, [id, importMaterials, afterMaterials, offerGeneration, say, generate, current, editSlide, respond]);

  /** Выбор из библиотеки — первый шаг построения в чате. */
  const selectTemplate = useCallback((templateId: string) => {
    if (current().template_id === templateId) return;
    updateProject(id, { template_id: templateId });
    appendMessage(id, { role: "assistant", kind: "template_card", template_id: templateId });
    if (current().package_id) offerGeneration();
    else say("Шаблон выбран. Опишите задачу презентации или добавьте материалы.");
  }, [id, current, offerGeneration, say]);

  /** Действие по ответу на вопрос о PPTX: разбор как шаблона или импорт как материала. */
  /**
   * Готовая презентация: тот же файл разбирается как шаблон (композиции слайдов) и импортируется
   * как содержание (тексты, факты), затем запускается генерация одного варианта original —
   * слайды переносятся как есть, а правки идут из чата по слайдам.
   */
  const openAsDeck = useCallback(async (fileId: string) => {
    const meta = current().files.find((f) => f.file_id === fileId);
    if (!meta) return;
    try {
      const tpl = await api.templates.upload(fileId);
      patchProjectFile(id, fileId, { kind: "template", template_id: tpl.template_id });
      const p = current();
      const briefFilled = p.brief.title.trim().length > 0;
      const pkg = await api.content.create([fileId], briefFilled ? { ...p.brief } : undefined);
      patchProjectFile(id, fileId, { package_id: pkg.package_id });
      updateProject(id, { template_id: tpl.template_id, package_id: pkg.package_id });
      const req: GenerationRequest = {
        schema_version: "1.2",
        template_id: tpl.template_id,
        package_id: pkg.package_id,
        idempotency_key: `deck-${id}-${fileId}-${Date.now().toString(36)}`,
        settings: { variants: ["original"], language: p.brief.language || "ru", run_contextual_audit: p.settings.contextual, generate_images: false },
      };
      const job = await api.generations.create(req);
      updateProject(id, { job_id: job.job_id, chosen_variant: null });
    } catch (e) {
      say(`Не удалось открыть «${meta.name}» как готовую презентацию: ${e instanceof ApiError ? e.message : "неизвестная ошибка"}.`);
    }
  }, [id, say, current]);

  const applyTemplateAnswer = useCallback(async (fileId: string, answer: PptxAnswer) => {
    if (answer === "template") {
      await uploadTemplate(fileId);
    } else if (answer === "deck") {
      await openAsDeck(fileId);
      return;
    } else {
      patchProjectFile(id, fileId, { kind: "material" });
      say("Хорошо, PPTX считаю материалом: текст его слайдов пойдёт в содержание.");
      await importMaterials();
      await afterMaterials();
      return;
    }
    if (current().template_id && current().package_id) offerGeneration();
  }, [id, uploadTemplate, openAsDeck, importMaterials, afterMaterials, offerGeneration, say, current]);

  const resolveTemplateQuestion = useCallback(async (messageId: string, fileId: string, answer: PptxAnswer) => {
    void patchMessage(id, messageId, { resolved: answer });
    await applyTemplateAnswer(fileId, answer);
  }, [id, applyTemplateAnswer]);

  /**
   * PPTX из чата: вопрос появляется в ленте в момент броска, файл тем временем грузится на сервер.
   * Когда загрузка завершилась, сообщение и вопрос записываются в историю уже с ответом, если он был дан.
   */
  const stagePptx = useCallback((file: File) => {
    const localId = `stg_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
    setStaged((s) => [...s, { local_id: localId, name: file.name, size: file.size }]);
    void (async () => {
      let row: ProjectFile | undefined;
      try {
        row = (await addProjectFiles(id, [file]))[0];
      } catch (e) {
        say(`Не удалось загрузить «${file.name}»: ${e instanceof ApiError ? e.message : "неизвестная ошибка"}.`);
      }
      const answer = stagedAnswers.current.get(localId);
      stagedAnswers.current.delete(localId);
      setStaged((s) => s.filter((x) => x.local_id !== localId));
      if (!row) return;
      appendMessage(id, { role: "user", kind: "message", text: "", file_ids: [row.file_id] });
      if (row.kind === "template") {
        // Те же байты уже разобраны как шаблон этого проекта: вопрос не нужен.
        if (row.template_id && current().template_id !== row.template_id) selectTemplate(row.template_id);
        else say(`«${row.name}» уже используется как шаблон проекта.`);
        return;
      }
      appendMessage(id, { role: "assistant", kind: "template_question", file_id: row.file_id, ...(answer ? { resolved: answer } : {}) });
      if (answer) await applyTemplateAnswer(row.file_id, answer);
    })();
  }, [id, say, current, applyTemplateAnswer, selectTemplate]);

  /** Ответ на вопрос о PPTX, который ещё грузится: запоминается и применяется после загрузки. */
  const answerStaged = useCallback((localId: string, answer: PptxAnswer) => {
    stagedAnswers.current.set(localId, answer);
    setStaged((s) => s.map((x) => (x.local_id === localId ? { ...x, answer } : x)));
  }, []);

  /** Файлы из дропзоны или скрепки: презентации уходят в работу сразу, остальные возвращаются как вложения к сообщению. */
  const attach = useCallback((files: File[]): File[] => {
    files.filter(isPptxFile).forEach(stagePptx);
    return files.filter((f) => !isPptxFile(f));
  }, [stagePptx]);

  const setPurpose = useCallback(async (purpose: BriefDraft["purpose"]) => {
    updateProject(id, (p) => ({ brief: { ...p.brief, purpose } }));
    const p = current();
    if (p.package_id) await importMaterials();
  }, [id, importMaterials, current]);

  const removeFile = useCallback(async (fileId: string) => {
    const meta = current().files.find((f) => f.file_id === fileId);
    if (!meta) return;
    await removeProjectFile(id, fileId);
    if (meta.kind === "template" && meta.template_id === current().template_id) {
      updateProject(id, { template_id: null });
      say(`Шаблон «${meta.name}» убран из проекта. Он остаётся в библиотеке шаблонов, выбрать другой можно справа.`);
    } else if (meta.kind === "material") {
      const pkg = await importMaterials();
      if (!pkg) {
        updateProject(id, { package_id: null });
        say(`Материал «${meta.name}» удалён. Материалов больше нет: добавьте файлы или опишите задачу в чате.`);
      } else {
        say(`Материал «${meta.name}» удалён, содержание переимпортировано без него.`);
      }
    }
  }, [id, importMaterials, say, current]);

  /** Явная загрузка шаблона из выбора шаблона — без вопроса о назначении файла. */
  const addTemplate = useCallback(async (file: File) => {
    let rows: ProjectFile[] = [];
    try {
      rows = await addProjectFiles(id, [file]);
    } catch (e) {
      notifications.show({ color: "red", title: "Шаблон не загружен", message: e instanceof ApiError ? e.message : "Неизвестная ошибка" });
      return;
    }
    const row = rows[0];
    if (!row) return;
    await uploadTemplate(row.file_id);
    await refreshProject(id);
    if (current().template_id && current().package_id) offerGeneration();
    else if (current().template_id) say("Шаблон добавлен. Опишите задачу презентации или добавьте материалы.");
  }, [id, uploadTemplate, offerGeneration, current, say]);

  // Карточка задания при запуске. Служебные отчёты не добавляются в разговор.
  useEffect(() => {
    const jobId = project.job_id;
    if (!jobId) return;
    if (!project.events.some((m) => m.role === "assistant" && m.kind === "job_card" && m.job_id === jobId)) {
      appendMessage(id, { role: "assistant", kind: "job_card", job_id: jobId });
    }
  }, [id, project.job_id, project.events]);


  const needsDesignMode = Boolean(project.template_id && (project.package_id || project.brief.title) && !project.settings.design_mode && !project.job_id);
  useEffect(() => {
    if (needsDesignMode && !current().events.some((event) => event.kind === "text" && event.text === DESIGN_MODE_QUESTION)) say(DESIGN_MODE_QUESTION);
  }, [needsDesignMode, project.events, say, current]);

  const notifyError = (title: string, e: unknown) => notifications.show({ color: "red", title, message: e instanceof ApiError ? e.message : "Неизвестная ошибка" });

  return { send, suggestions: needsDesignMode ? DESIGN_MODES.map((mode) => mode.label) : suggestions, attach, staged, answerStaged, resolveTemplateQuestion, setPurpose, removeFile, importMaterials, selectTemplate, addTemplate, notifyError };
}

export type Chat = ReturnType<typeof useChat>;
