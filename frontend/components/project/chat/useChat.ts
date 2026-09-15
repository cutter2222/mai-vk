"use client";

import { notifications } from "@mantine/notifications";
import { useCallback, useEffect } from "react";

import { api, ApiError } from "@/lib/api/client";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";
import { fileStore } from "@/lib/state/fileStore";
import {
  addProjectFiles,
  appendMessage,
  getProject,
  newFileId,
  patchMessage,
  patchProjectFile,
  removeProjectFile,
  updateProject,
  type BriefDraft,
  type Project,
  type ProjectFile,
  type SettingsDraft,
} from "@/lib/state/projects";

const MATERIAL_EXT = /\.(docx|xlsx|csv|pdf|md|txt|png|jpe?g)$/i;
const TEMPLATE_EXT = /\.pptx$/i;
const GENERATE_RE = /сгенерир|запусти|собери|сделай|построй|начина/i;
const EDIT_RE = /поменя[йть]|перестав|местами|удали|убери|добавь слайд|переимен/i;

const isTemplateFile = (f: File) => TEMPLATE_EXT.test(f.name);
const isMaterialFile = (f: File) => MATERIAL_EXT.test(f.name);

/**
 * Оркестратор чата. Файлы распознаются по расширению без модели; бриф из текста извлекает сервер;
 * каждый шаг конвейера появляется в чате карточкой, которая читает живое состояние проекта.
 */
export function useChat(project: Project, session: GenerationSession, generate: () => Promise<boolean>) {
  const id = project.id;
  const current = useCallback(() => getProject(id) as Project, [id]);

  const say = useCallback((text: string) => appendMessage(id, { role: "assistant", kind: "text", text }), [id]);

  /** Импорт всех материалов проекта в новый пакет. Возвращает package_id или null. */
  const importMaterials = useCallback(async (): Promise<string | null> => {
    const p = current();
    const materials = p.files.filter((f) => f.kind === "material");
    const withBytes = materials.map((f) => ({ meta: f, file: fileStore.get(f.id) })).filter((x): x is { meta: ProjectFile; file: File } => Boolean(x.file));
    const briefFilled = p.brief.title.trim().length > 0;
    if (withBytes.length === 0 && !briefFilled) return null;
    if (withBytes.length < materials.length) {
      say("Часть материалов была загружена до перезагрузки страницы, их байты не сохранились. Добавьте эти файлы снова, если они нужны.");
    }
    try {
      const res = await api.content.create(withBytes.map((x) => x.file), briefFilled ? { ...p.brief } : undefined);
      withBytes.forEach((x) => patchProjectFile(id, x.meta.id, { package_id: res.package_id }));
      updateProject(id, { package_id: res.package_id });
      appendMessage(id, { role: "assistant", kind: "content_card", package_id: res.package_id, file_ids: withBytes.map((x) => x.meta.id) });
      return res.package_id;
    } catch (e) {
      say(`Не удалось импортировать материалы: ${e instanceof ApiError ? e.message : "неизвестная ошибка"}.`);
      return null;
    }
  }, [id, say, current]);

  const uploadTemplate = useCallback(async (fileId: string) => {
    const meta = current().files.find((f) => f.id === fileId);
    const file = fileStore.get(fileId);
    if (!meta) return;
    if (!file) {
      say(`Файл «${meta.name}» был добавлен до перезагрузки страницы, его содержимое не сохранилось. Перетащите его в чат ещё раз.`);
      return;
    }
    try {
      const res = await api.templates.upload(file);
      patchProjectFile(id, fileId, { kind: "template", template_id: res.template_id });
      updateProject(id, { template_id: res.template_id });
      appendMessage(id, { role: "assistant", kind: "template_card", template_id: res.template_id });
    } catch (e) {
      say(`Не удалось загрузить шаблон: ${e instanceof ApiError ? e.message : "неизвестная ошибка"}.`);
    }
  }, [id, say, current]);

  /** После шага проверяет, всё ли готово к генерации, и показывает карточку брифа с кнопкой. */
  const offerGeneration = useCallback((understood: string[] = []) => {
    const p = current();
    const last = p.messages[p.messages.length - 1];
    if (last && last.role === "assistant" && last.kind === "brief_card") return;
    appendMessage(id, { role: "assistant", kind: "brief_card", understood, missing_purpose: !p.brief.purpose });
  }, [id, current]);

  const send = useCallback(async (text: string, files: File[]) => {
    const trimmed = text.trim();
    if (!trimmed && files.length === 0) return;

    // 1. Файлы попадают в проект сразу: байты в память, метаданные в реестр.
    const metas: ProjectFile[] = files.map((f) => {
      const fid = newFileId();
      fileStore.put(fid, f);
      return { id: fid, name: f.name, size: f.size, mime: f.type, kind: isTemplateFile(f) ? "other" : isMaterialFile(f) ? "material" : "other", added_at: new Date().toISOString() };
    });
    if (metas.length) addProjectFiles(id, metas);
    appendMessage(id, { role: "user", text: trimmed, file_ids: metas.map((m) => m.id) });

    // 2. PPTX может быть и шаблоном, и материалом: спрашиваем.
    const pptx = metas.filter((m, i) => isTemplateFile(files[i]));
    pptx.forEach((m) => appendMessage(id, { role: "assistant", kind: "template_question", file_id: m.id }));

    // 3. Неподдерживаемые типы остаются в файлах проекта.
    const others = metas.filter((m, i) => !isTemplateFile(files[i]) && !isMaterialFile(files[i]));
    if (others.length) {
      say(`${others.map((o) => `«${o.name}»`).join(", ")}: такой тип файла сохранён в файлах проекта, но при генерации пока не используется. Поддерживаются docx, xlsx, csv, pdf, md, txt и изображения.`);
    }

    // 4. Материалы импортируются пакетом вместе с уже загруженными.
    const materials = metas.filter((m) => m.kind === "material");
    let imported = false;
    if (materials.length) {
      imported = Boolean(await importMaterials());
    }

    // 5. Текст: бриф извлекает сервер; поля, которых нет в сообщении, не трогаем.
    let understood: string[] = [];
    if (trimmed) {
      if (EDIT_RE.test(trimmed) && current().job_id) {
        say("Перестановка, удаление и переименование слайдов появятся вместе с серверной операцией правки плана. Пока можно скачать PPTX и поправить в PowerPoint или изменить бриф и сгенерировать заново.");
      }
      try {
        const res = await api.brief.extract(trimmed, { ...current().brief });
        understood = res.understood;
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
      if (!understood.length && !metas.length && !EDIT_RE.test(trimmed)) {
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
      offerGeneration(understood);
    }
    const ready = current().template_id && current().package_id && current().brief.purpose;
    if (trimmed && GENERATE_RE.test(trimmed) && ready && !current().job_id) {
      await generate();
    }
  }, [id, importMaterials, offerGeneration, say, generate, current]);

  const resolveTemplateQuestion = useCallback(async (messageId: string, fileId: string, answer: "template" | "material") => {
    patchMessage(id, messageId, { resolved: answer });
    if (answer === "template") {
      await uploadTemplate(fileId);
    } else {
      patchProjectFile(id, fileId, { kind: "material" });
      say("Хорошо, PPTX считаю материалом: текст его слайдов пойдёт в содержание.");
      await importMaterials();
    }
    if (current().template_id && current().package_id) offerGeneration();
  }, [id, uploadTemplate, importMaterials, offerGeneration, say, current]);

  const setPurpose = useCallback(async (purpose: string) => {
    updateProject(id, (p) => ({ brief: { ...p.brief, purpose } }));
    const p = current();
    if (p.package_id) await importMaterials();
  }, [id, importMaterials, current]);

  const removeFile = useCallback(async (fileId: string) => {
    const meta = current().files.find((f) => f.id === fileId);
    if (!meta) return;
    removeProjectFile(id, fileId);
    fileStore.remove(fileId);
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

  /** Шаблон из библиотеки, выбранный в шапке: карточка в чате, чтобы история отражала смену оформления. */
  const selectTemplate = useCallback((templateId: string) => {
    if (current().template_id === templateId) return;
    updateProject(id, { template_id: templateId });
    appendMessage(id, { role: "assistant", kind: "template_card", template_id: templateId });
  }, [id, current]);

  /** PPTX, выбранный в шапке как шаблон: без вопроса «шаблон или материал». */
  const addTemplate = useCallback(async (file: File) => {
    const fid = newFileId();
    fileStore.put(fid, file);
    addProjectFiles(id, [{ id: fid, name: file.name, size: file.size, mime: file.type, kind: "template", added_at: new Date().toISOString() }]);
    appendMessage(id, { role: "user", text: "", file_ids: [fid] });
    await uploadTemplate(fid);
    if (current().template_id && current().package_id) offerGeneration();
  }, [id, uploadTemplate, offerGeneration, current]);

  // Карточка задания при запуске и карточка аудита по завершении.
  useEffect(() => {
    const jobId = project.job_id;
    if (!jobId) return;
    if (!project.messages.some((m) => m.role === "assistant" && m.kind === "job_card" && m.job_id === jobId)) {
      appendMessage(id, { role: "assistant", kind: "job_card", job_id: jobId });
    }
  }, [id, project.job_id, project.messages]);

  useEffect(() => {
    const jobId = session.jobId;
    if (!jobId || !session.terminal || !session.result) return;
    if (session.result.status === "canceled") return;
    if (project.messages.some((m) => m.role === "assistant" && m.kind === "audit_card" && m.job_id === jobId)) return;
    appendMessage(id, { role: "assistant", kind: "audit_card", job_id: jobId });
  }, [id, session.jobId, session.terminal, session.result, project.messages]);

  const notifyError = (title: string, e: unknown) => notifications.show({ color: "red", title, message: e instanceof ApiError ? e.message : "Неизвестная ошибка" });

  return { send, resolveTemplateQuestion, setPurpose, removeFile, importMaterials, selectTemplate, addTemplate, notifyError };
}

export type Chat = ReturnType<typeof useChat>;
