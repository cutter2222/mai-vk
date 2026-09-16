"use client";

import { notifications } from "@mantine/notifications";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api/client";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";
import {
  addProjectFiles,
  appendMessage,
  getProject,
  patchMessage,
  patchProjectFile,
  refreshProject,
  removeProjectFile,
  updateProject,
  type BriefDraft,
  type Project,
  type ProjectFile,
  type SettingsDraft,
} from "@/lib/state/projects";

const GENERATE_RE = /сгенерир|запусти|собери|сделай|построй|начина/i;
const EDIT_RE = /поменя[йть]|перестав|местами|удали|убери|добавь слайд|переимен/i;

const isPptx = (f: ProjectFile) => f.check.format === "pptx";
const isMaterial = (f: ProjectFile) => f.kind === "material";
const isPptxFile = (f: File) => /\.pptx$/i.test(f.name);

/** PPTX, брошенный в чат: вопрос «шаблон или материал» показан сразу, файл ещё едет на сервер. */
export interface StagedPptx {
  local_id: string;
  name: string;
  size: number;
  answer?: "template" | "material";
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
  const stagedAnswers = useRef(new Map<string, "template" | "material">());

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

  const uploadTemplate = useCallback(async (fileId: string) => {
    const meta = current().files.find((f) => f.file_id === fileId);
    if (!meta) return;
    try {
      const res = await api.templates.upload(fileId);
      patchProjectFile(id, fileId, { kind: "template", template_id: res.template_id });
      updateProject(id, { template_id: res.template_id });
      appendMessage(id, { role: "assistant", kind: "template_card", template_id: res.template_id });
    } catch (e) {
      say(`Не удалось загрузить шаблон: ${e instanceof ApiError ? e.message : "неизвестная ошибка"}.`);
    }
  }, [id, say, current]);

  /** После шага проверяет, всё ли готово к генерации, и показывает карточку брифа с кнопкой. */
  const offerGeneration = useCallback((understood: string[] = [], briefSource?: "model" | "heuristic") => {
    const p = current();
    const last = p.events[p.events.length - 1];
    if (last && last.role === "assistant" && last.kind === "brief_card") return;
    appendMessage(id, { role: "assistant", kind: "brief_card", understood, missing_purpose: !p.brief.purpose, ...(briefSource ? { brief_source: briefSource } : {}) });
  }, [id, current]);

  const send = useCallback(async (text: string, files: File[]) => {
    const trimmed = text.trim();
    if (!trimmed && files.length === 0) return;

    // 1. Файлы попадают на сервер сразу: проверка и дедупликация там, в проекте остаются записи.
    let rows: ProjectFile[] = [];
    if (files.length) {
      try {
        rows = await addProjectFiles(id, files);
      } catch (e) {
        say(`Не удалось загрузить файлы: ${e instanceof ApiError ? e.message : "неизвестная ошибка"}.`);
        if (!trimmed) return;
      }
    }
    appendMessage(id, { role: "user", kind: "message", text: trimmed, file_ids: rows.map((r) => r.file_id) });

    // 2. PPTX может быть и шаблоном, и материалом: спрашиваем.
    rows.filter((r) => isPptx(r) && r.kind !== "template").forEach((r) => appendMessage(id, { role: "assistant", kind: "template_question", file_id: r.file_id }));

    // 3. Неподдерживаемые типы остаются в файлах проекта.
    const others = rows.filter((r) => !isPptx(r) && r.kind === "other");
    if (others.length) {
      say(`${others.map((o) => `«${o.name}»`).join(", ")}: такой тип файла сохранён в файлах проекта, но при генерации пока не используется. Поддерживаются docx, xlsx, csv, pdf, md, txt и изображения.`);
    }

    // 4. Материалы импортируются пакетом вместе с уже загруженными.
    let imported = false;
    if (rows.some(isMaterial)) {
      imported = Boolean(await importMaterials());
    }

    // 5. Текст: бриф извлекает сервер; поля, которых нет в сообщении, не трогаем.
    let understood: string[] = [];
    let briefSource: "model" | "heuristic" | undefined;
    if (trimmed) {
      if (EDIT_RE.test(trimmed) && current().job_id) {
        say("Перестановка, удаление и переименование слайдов появятся вместе с серверной операцией правки плана. Пока можно скачать PPTX и поправить в PowerPoint или изменить бриф и сгенерировать заново.");
      }
      try {
        const res = await api.brief.extract(trimmed, { ...current().brief });
        understood = res.understood;
        briefSource = res.source;
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
      if (!understood.length && !rows.length && !EDIT_RE.test(trimmed)) {
        say("Не нашёл в сообщении ничего про презентацию. Опишите задачу одной фразой: «сделай презентацию про запуск сервиса умных уведомлений для руководителей, чтобы одобрили пилот» — и перетащите шаблон PPTX и материалы.");
        return;
      }
    }

    // 6. Бриф без файлов тоже содержание: пакет из одного брифа.
    const p = current();
    if (!p.package_id && p.brief.title.trim() && !imported) {
      imported = Boolean(await importMaterials());
    }

    // 7. Карточка брифа с кнопкой, когда есть что показать; явная команда запускает генерацию.
    if (understood.length || (current().template_id && current().package_id)) {
      offerGeneration(understood, understood.length ? briefSource : undefined);
    }
    const ready = current().template_id && current().package_id && current().brief.purpose;
    if (trimmed && GENERATE_RE.test(trimmed) && ready && !current().job_id) {
      await generate();
    }
  }, [id, importMaterials, offerGeneration, say, generate, current]);

  /** Шаблон из библиотеки, выбранный в шапке: карточка в чате, чтобы история отражала смену оформления. */
  const selectTemplate = useCallback((templateId: string) => {
    if (current().template_id === templateId) return;
    updateProject(id, { template_id: templateId });
    appendMessage(id, { role: "assistant", kind: "template_card", template_id: templateId });
  }, [id, current]);

  /** Действие по ответу на вопрос о PPTX: разбор как шаблона или импорт как материала. */
  const applyTemplateAnswer = useCallback(async (fileId: string, answer: "template" | "material") => {
    if (answer === "template") {
      await uploadTemplate(fileId);
    } else {
      patchProjectFile(id, fileId, { kind: "material" });
      say("Хорошо, PPTX считаю материалом: текст его слайдов пойдёт в содержание.");
      await importMaterials();
    }
    if (current().template_id && current().package_id) offerGeneration();
  }, [id, uploadTemplate, importMaterials, offerGeneration, say, current]);

  const resolveTemplateQuestion = useCallback(async (messageId: string, fileId: string, answer: "template" | "material") => {
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
  const answerStaged = useCallback((localId: string, answer: "template" | "material") => {
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
      say(`Шаблон «${meta.name}» убран из проекта. Он остаётся в библиотеке шаблонов, выбрать другой можно в карточке шаблона.`);
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

  /** PPTX, выбранный в шапке как шаблон: без вопроса «шаблон или материал». */
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
    appendMessage(id, { role: "user", kind: "message", text: "", file_ids: [row.file_id] });
    await uploadTemplate(row.file_id);
    await refreshProject(id);
    if (current().template_id && current().package_id) offerGeneration();
  }, [id, uploadTemplate, offerGeneration, current]);

  // Карточка задания при запуске и карточка аудита по завершении.
  useEffect(() => {
    const jobId = project.job_id;
    if (!jobId) return;
    if (!project.events.some((m) => m.role === "assistant" && m.kind === "job_card" && m.job_id === jobId)) {
      appendMessage(id, { role: "assistant", kind: "job_card", job_id: jobId });
    }
  }, [id, project.job_id, project.events]);

  useEffect(() => {
    const jobId = session.jobId;
    if (!jobId || !session.terminal || !session.result) return;
    if (session.result.status === "canceled") return;
    if (project.events.some((m) => m.role === "assistant" && m.kind === "audit_card" && m.job_id === jobId)) return;
    appendMessage(id, { role: "assistant", kind: "audit_card", job_id: jobId });
  }, [id, session.jobId, session.terminal, session.result, project.events]);

  const notifyError = (title: string, e: unknown) => notifications.show({ color: "red", title, message: e instanceof ApiError ? e.message : "Неизвестная ошибка" });

  return { send, attach, staged, answerStaged, resolveTemplateQuestion, setPurpose, removeFile, importMaterials, selectTemplate, addTemplate, notifyError };
}

export type Chat = ReturnType<typeof useChat>;
