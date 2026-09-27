"use client";

import { notifications } from "@mantine/notifications";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api/client";
import type { GenerationSession, SlideTarget } from "@/lib/hooks/useGenerationSession";
import {
  addProjectFiles,
  addProjectText,
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
import { isDesignModeReply } from "@/lib/designMode";
import { buildOrder, VARIANT_LABELS } from "@/lib/format";

const GENERATE_RE = /сгенерир|запусти|собер[иа]|сдела[йт]|сделаем|построй|начина|давай/i;
const EDIT_RE = /поменя[йть]|перестав|местами|удали|убери|добавь слайд|переимен/i;

/**
 * Сообщение несёт содержание презентации, а не только задачу: раскладка «Слайд 1: …, Слайд 2: …»
 * или объём, который в бриф не помещается. Такое сообщение становится ещё и материалом
 * («Текст из чата.md»): тезисы и цифры доходят до слайдов. То же правило — `chat_text.is_content_text`.
 */
function isContentText(text: string): boolean {
  const marks = [...text.matchAll(/(?:^|[\s.!?…:;)»\]])(?:слайд|slide)\s*№?\s*(\d{1,2})\s*[:.)—–-]/gi)].map((m) => Number(m[1]));
  const outline = marks.length >= 2 && marks[0] === 1 && marks.every((n, i) => i === 0 || n > marks[i - 1]);
  return outline || text.trim().length >= 1200;
}

const isPptx = (f: ProjectFile) => f.check.format === "pptx";
const isMaterial = (f: ProjectFile) => f.kind === "material";
const isPptxFile = (f: File) => /\.pptx$/i.test(f.name);

/**
 * Что услышано из сообщения — одной фразой перед запуском сборки: «Понял задачу: «Тема»,
 * аудитория — …». Ошибиться видно сразу, поправить можно словами.
 */
function understoodLine(brief: BriefDraft): string {
  const title = brief.title.trim();
  if (!title) return "";
  const audience = brief.audience.trim();
  const short = audience.length > 90 ? `${audience.slice(0, 90).replace(/[,;\s]+\S*$/, "")}…` : audience;
  return `Понял задачу: «${title}»${short ? `, аудитория — ${short.charAt(0).toLowerCase()}${short.slice(1)}` : ""}. `;
}

/** Открытие готовой презентации в этой вкладке: файл, начало отсчёта, идёт ли подготовка до задания. */
export interface DeckStart {
  fileId: string;
  since: string;
  preparing: boolean;
}

/** PPTX, брошенный в чат: вопрос «шаблон, готовая презентация или материал» показан сразу, файл ещё едет на сервер. */
export interface StagedPptx {
  local_id: string;
  name: string;
  size: number;
  answer?: PptxAnswer;
  /** Какая доля файла уже ушла на сервер (0–1); нет — браузер ещё не сообщил. */
  sent?: number;
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
  // Готовая презентация открывается: с какого момента (таймер в чате) и идёт ли ещё подготовка
  // до задания — загрузка шаблона и содержания занимает секунды, а задания ещё нет.
  const [deckStart, setDeckStart] = useState<DeckStart | null>(null);
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

  /**
   * Тихий импорт после загрузки: в ленте ничего не появляется, пока разбор не упал. Опрос
   * ограничен десятью минутами; ошибку говорим, только если пакет ещё текущий у проекта.
   */
  const watchImport = useCallback(async (packageId: string) => {
    const until = Date.now() + 10 * 60_000;
    for (let delay = 1500; Date.now() < until; delay = Math.min(delay * 1.5, 5000)) {
      await new Promise((resolve) => setTimeout(resolve, delay));
      let detail;
      try {
        detail = await api.content.get(packageId);
      } catch {
        return;
      }
      if (detail.status === "succeeded") return;
      if (detail.status === "failed") {
        if (current().package_id === packageId) say(`Не смог прочитать материалы: ${(detail.error?.message ?? "импорт не удался").split("\n")[0]}. Удалите файл во вкладке «Файлы» или загрузите его заново.`);
        return;
      }
    }
  }, [say, current]);

  /**
   * Импорт всех материалов проекта в новый пакет по file_ids. Возвращает package_id или null.
   * `quiet` — импорт после загрузки файлов: без карточки «Прочитал…», только ошибка.
   */
  const importMaterials = useCallback(async ({ quiet = false }: { quiet?: boolean } = {}): Promise<string | null> => {
    const p = current();
    const materials = p.files.filter(isMaterial);
    const briefFilled = p.brief.title.trim().length > 0;
    if (materials.length === 0 && !briefFilled) return null;
    try {
      const res = await api.content.create(materials.map((f) => f.file_id), briefFilled ? { ...p.brief } : undefined);
      materials.forEach((f) => patchProjectFile(id, f.file_id, { package_id: res.package_id }));
      updateProject(id, { package_id: res.package_id });
      if (quiet) void watchImport(res.package_id);
      else appendMessage(id, { role: "assistant", kind: "content_card", package_id: res.package_id, file_ids: materials.map((f) => f.file_id) });
      return res.package_id;
    } catch (e) {
      say(`Не удалось импортировать материалы: ${e instanceof ApiError ? e.message : "неизвестная ошибка"}.`);
      return null;
    }
  }, [id, say, current, watchImport]);

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
   * Шаблон и содержание есть, презентации ещё нет — сборка стартует сразу, без карточки с кнопкой:
   * назначение и режим оформления берутся по умолчанию, уточнить их можно словами. Шаблона нет —
   * просьба выбрать его. Презентация уже есть — карточка задачи с «Сгенерировать заново»:
   * пересборка сама не начинается. `lead` — фраза перед этим («Понял задачу…», «Выбрал шаблон…»).
   */
  const proceed = useCallback(async (lead = "") => {
    const p = current();
    if (!p.package_id) {
      if (lead) say(lead.trim());
      return;
    }
    // Упавшая целиком сборка презентацией не считается: новое содержание собирается сразу.
    if (p.job_id && session.result?.status !== "failed") {
      if (lead) say(lead.trim());
      offerGeneration();
      return;
    }
    if (!p.template_id) {
      say(`${lead}Выберите шаблон оформления вверху справа — и я сразу начну собирать.`);
      return;
    }
    // Порядок сборки словами: «сначала сбалансированный вариант, следом компактный и подробный».
    const [first, ...next] = buildOrder(p.settings.variants).map((v) => (VARIANT_LABELS[v] ?? v).toLowerCase());
    const after = next.length > 1 ? `${next.slice(0, -1).join(", ")} и ${next[next.length - 1]}` : next[0];
    say(`${lead}${after ? `Собираю презентацию: сначала ${first} вариант, следом ${after}.` : "Собираю презентацию."}`);
    await generate();
  }, [current, say, offerGeneration, generate, session.result?.status]);

  /**
   * Материалы импортированы без задачи в тексте: без шаблона — просьба выбрать его, с шаблоном —
   * презентация собирается сразу (назначение можно уточнить в карточке задачи и пересобрать).
   * После простой загрузки файлов (`quiet`) чат не просит выбрать шаблон — об этом говорит
   * правая панель, — а при готовой презентации молчит совсем: новые файлы нужны для слайдов,
   * а не для пересборки.
   */
  const afterMaterials = useCallback(async ({ quiet = false }: { quiet?: boolean } = {}) => {
    const p = current();
    if (!p.package_id) return;
    if (quiet && p.job_id) return;
    if (!p.template_id) {
      if (!quiet) say("Материалы в работе. Шаблон оформления не выбран: выберите его вверху справа — и я сразу начну собирать.");
      return;
    }
    await proceed();
  }, [current, say, proceed]);

  /** Сообщение, адресованное слайду: событие с адресом, запрос правки, карточка хода и результата. */
  const editSlide = useCallback(async (text: string, target: SlideTarget, { silent = false }: { silent?: boolean } = {}): Promise<string | null> => {
    const slideRef = { job_id: target.jobId, variant_id: target.variantId, revision: target.revision, slide_index: target.slideIndex };
    // Роутер уже записал сообщение человека: повторять его не нужно.
    if (!silent) appendMessage(id, { role: "user", kind: "message", text, file_ids: [], slide_ref: slideRef });
    try {
      const editJobId = await session.requestEdit(target, text);
      appendMessage(id, { role: "assistant", kind: "edit_card", job_id: target.jobId, variant_id: target.variantId, edit_job_id: editJobId, slide_index: target.slideIndex });
      return editJobId;
    } catch (e) {
      const code = e instanceof ApiError ? e.code : "";
      if (code === "revision_stale") say("Ревизия слайда устарела: справа уже новая. Обновил результат — повторите просьбу к актуальному слайду.");
      else if (code === "repair_in_progress") say("Предыдущая правка этого варианта ещё применяется. Дождитесь её и повторите просьбу.");
      else if (code === "variant_failed") say(target.variantId === "original" && e instanceof ApiError ? `${e.message.replace(/\.\s*$/, "")}.` : "Этот вариант не собран, править в нём нечего. Выберите другой вариант.");
      else say(`Не удалось запустить правку: ${e instanceof ApiError ? e.message : "неизвестная ошибка"}.`);
      return null;
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
    // Загрузка без текста в ленту не пишется: файлы видны во вкладке «Файлы».
    const message = rest ? appendMessage(id, { role: "user", kind: "message", text: rest, file_ids: rows.map((r) => r.file_id) }) : null;
    if (message && isDesignModeReply(rest)) {
      await respond(message.event_id);
      await refreshProject(id);
      if (!files.length) return;
      rest = ""; // Выбор режима не становится темой брифа при отправке с вложениями.
    }
    // A conversation about an existing deck must not become a new brief or an office edit.
    // Сборка, у которой не вышло ни одного варианта, колодой не считается: новое содержание — новая задача.
    const noDeck = session.result?.status === "failed" && isContentText(rest);
    if (message && !files.length && current().job_id && !noDeck) {
      await respond(message.event_id);
      return;
    }

    // 2. PPTX может быть и шаблоном, и материалом: спрашиваем.
    rows.filter((r) => isPptx(r) && r.kind !== "template").forEach((r) => appendMessage(id, { role: "assistant", kind: "template_question", file_id: r.file_id }));

    // 3. Неподдерживаемые типы молча остаются в файлах проекта: карточка там подписана
    //    «не используется при генерации».

    // 4. Материалы импортируются пакетом вместе с уже загруженными, без карточки в ленте;
    //    без текста задачи презентация собирается сразу, если выбран шаблон.
    let imported = false;
    if (rows.some(isMaterial)) {
      imported = Boolean(await importMaterials({ quiet: true }));
      if (imported && !rest) {
        await afterMaterials({ quiet: true });
        return;
      }
    }

    // 5. Текст: бриф извлекает сервер; поля, которых нет в сообщении, не трогаем.
    let understood: string[] = [];
    // Намерение собрать презентацию: сервер (модель или правила) либо явная команда в тексте.
    let wantsGeneration = Boolean(rest) && GENERATE_RE.test(rest);
    // Сообщение с содержанием — ещё и материал: бриф берёт из него тему и число слайдов,
    // а тезисы, цифры и раскладку по слайдам импорт разбирает как документ.
    let contentText = false;
    if (rest && isContentText(rest)) {
      try {
        await addProjectText(id, rest);
        contentText = true;
        wantsGeneration = true;
      } catch (e) {
        say(`Не удалось сохранить текст как материал: ${e instanceof ApiError ? e.message : "ошибка сервера"}. Приложите его файлом .txt или .md.`);
      }
    }
    if (rest) {
      if (EDIT_RE.test(rest) && current().job_id) {
        say("Чтобы изменить слайд, выберите его в ленте справа: в поле ввода появится метка слайда, и просьба применится к нему новой ревизией. Перестановка и удаление слайдов пока недоступны.");
      }
      try {
        const res = await api.brief.extract(rest, { ...current().brief });
        understood = res.understood;
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
            imported = Boolean(await importMaterials({ quiet: true }));
          }
        }
      } catch {
        /* сервер не ответил: бриф можно заполнить вручную через «Изменить» */
      }
      if (message && !understood.length && !rows.length && !EDIT_RE.test(rest)) {
        await respond(message.event_id);
        if (!wantsGeneration) return;
      }
    }

    // Текст из чата уже среди материалов: пакет собирается заново вместе с брифом.
    if (contentText && !imported) imported = Boolean(await importMaterials({ quiet: true }));

    // 6. Бриф без файлов тоже содержание: пакет из одного брифа.
    const p = current();
    if (!p.package_id && p.brief.title.trim() && !imported) {
      imported = Boolean(await importMaterials({ quiet: true }));
    }

    // 7. Задача понята или просят собрать — сборка стартует сразу, если есть шаблон и
    //    содержание; иначе чат просит выбрать шаблон. Карточки с кнопкой и выбором назначения нет.
    if (understood.length || wantsGeneration || imported) {
      await proceed(understood.length ? understoodLine(current().brief) : "");
    }
  }, [id, importMaterials, afterMaterials, proceed, say, current, editSlide, respond, session.result?.status]);

  /**
   * Выбор уже разобранного шаблона (из библиотеки, из «Файлов», повторный бросок того же PPTX):
   * одна фраза с названием. Карточка «Разобрал шаблон… за N с» — только при загрузке файла
   * шаблоном: здесь разбора не было, и отчёт о нём путает.
   */
  const selectTemplate = useCallback((templateId: string, name: string) => {
    if (current().template_id === templateId) return;
    updateProject(id, { template_id: templateId });
    const title = name.replace(/\.pptx$/i, "");
    if (current().package_id) void proceed(`Выбрал шаблон «${title}». `);
    else say(`Выбрал шаблон «${title}». Загрузите материалы или опишите задачу для дальнейшей работы.`);
  }, [id, current, proceed, say]);

  /**
   * Готовая презентация, проверенная моделью: то же задание с контекстным аудитом. Копия и
   * диаграммы берутся из кэша, презентация остаётся открытой, итог проверки скажет лента.
   */
  const recheckDeck = useCallback(async () => {
    const request = session.result?.request;
    if (!request) return;
    try {
      const job = await api.generations.create({
        ...request,
        idempotency_key: `deck-audit-${id}-${Date.now().toString(36)}`,
        settings: { ...request.settings, run_contextual_audit: true },
      });
      updateProject(id, { job_id: job.job_id, chosen_variant: null });
      say("Проверяю слайды моделью по Приложению 1 — это пара минут, презентация остаётся открытой.");
    } catch (e) {
      say(`Не удалось запустить проверку: ${e instanceof ApiError ? e.message : "неизвестная ошибка"}.`);
    }
  }, [id, say, session.result?.request]);

  /** Действие по ответу на вопрос о PPTX: разбор как шаблона или импорт как материала. */
  /**
   * Готовая презентация: тот же файл разбирается как шаблон (композиции слайдов) и импортируется
   * как содержание (тексты, факты), затем запускается генерация одного варианта original —
   * слайды переносятся как есть, а правки идут из чата по слайдам.
   * Шаблон, содержание и задание попадают в проект одним обновлением: проект с шаблоном и
   * содержанием, но без задания, — это новая презентация, и чат спросил бы о режиме оформления.
   */
  const openAsDeck = useCallback(async (fileId: string) => {
    const meta = current().files.find((f) => f.file_id === fileId);
    if (!meta) return;
    setDeckStart({ fileId, since: new Date().toISOString(), preparing: true });
    try {
      const tpl = await api.templates.upload(fileId);
      patchProjectFile(id, fileId, { kind: "template", template_id: tpl.template_id });
      const p = current();
      const briefFilled = p.brief.title.trim().length > 0;
      const pkg = await api.content.create([fileId], briefFilled ? { ...p.brief } : undefined);
      patchProjectFile(id, fileId, { package_id: pkg.package_id });
      // Контекстный аудит (модель по картинкам слайдов, около двух минут) для готовой
      // презентации выключен: его не просили, а держал он воркер. Включить — «Проверить
      // слайды моделью» в подробностях задания.
      const req: GenerationRequest = {
        schema_version: "1.2",
        template_id: tpl.template_id,
        package_id: pkg.package_id,
        idempotency_key: `deck-${id}-${fileId}-${Date.now().toString(36)}`,
        settings: { variants: ["original"], language: p.brief.language || "ru", run_contextual_audit: false, generate_images: false },
      };
      const job = await api.generations.create(req);
      updateProject(id, { template_id: tpl.template_id, package_id: pkg.package_id, job_id: job.job_id, chosen_variant: null });
      setDeckStart((s) => (s?.fileId === fileId ? { ...s, preparing: false } : s));
    } catch (e) {
      setDeckStart(null);
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
    if (current().template_id && current().package_id) await proceed();
  }, [id, uploadTemplate, openAsDeck, importMaterials, afterMaterials, proceed, say, current]);

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
    // Ход загрузки под вопросом: лента перерисовывается на каждый новый процент, а не на каждое событие.
    let percent = -1;
    const onProgress = (share: number) => {
      const next = Math.floor(share * 100);
      if (next === percent) return;
      percent = next;
      setStaged((s) => s.map((x) => (x.local_id === localId ? { ...x, sent: share } : x)));
    };
    void (async () => {
      let row: ProjectFile | undefined;
      try {
        row = (await addProjectFiles(id, [file], onProgress))[0];
      } catch (e) {
        say(`Не удалось загрузить «${file.name}»: ${e instanceof ApiError ? e.message : "неизвестная ошибка"}.`);
      }
      const answer = stagedAnswers.current.get(localId);
      stagedAnswers.current.delete(localId);
      setStaged((s) => s.filter((x) => x.local_id !== localId));
      if (!row) return;
      // Сам бросок в ленту не пишется: вопрос ниже называет файл.
      if (row.kind === "template") {
        // Те же байты уже разобраны как шаблон этого проекта: вопрос не нужен.
        if (row.template_id && current().template_id !== row.template_id) selectTemplate(row.template_id, row.name);
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
      say(`Шаблон «${meta.name}» убран из проекта. Он остаётся в библиотеке шаблонов, выбрать другой можно вверху справа.`);
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
    if (current().template_id && current().package_id) await proceed();
    else if (current().template_id) say("Шаблон добавлен. Опишите задачу презентации или добавьте материалы.");
  }, [id, uploadTemplate, proceed, current, say]);

  // Карточка задания при запуске. Служебные отчёты не добавляются в разговор.
  useEffect(() => {
    const jobId = project.job_id;
    if (!jobId) return;
    if (!project.events.some((m) => m.role === "assistant" && m.kind === "job_card" && m.job_id === jobId)) {
      appendMessage(id, { role: "assistant", kind: "job_card", job_id: jobId });
    }
  }, [id, project.job_id, project.events]);



  const notifyError = (title: string, e: unknown) => notifications.show({ color: "red", title, message: e instanceof ApiError ? e.message : "Неизвестная ошибка" });

  return { send, respond, suggest: setSuggestions, say, editSlide, suggestions, attach, staged, answerStaged, resolveTemplateQuestion, setPurpose, removeFile, importMaterials, selectTemplate, addTemplate, notifyError, deckStart, recheckDeck };
}

export type Chat = ReturnType<typeof useChat>;
