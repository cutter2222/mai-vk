"use client";

import { notifications } from "@mantine/notifications";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api/client";
import type { ContentPackage, Override, TemplateProfile } from "@/lib/api/types";
import {
  applyOverrides,
  describeDraft,
  isNewText,
  mergeDraft,
  NEW_TEXT_PREFIX,
  patchSlides,
  resetObject as resetObjectOverrides,
  sameOverrides,
  type AssetResolver,
  type DeckSlide,
} from "@/lib/editor/overrides";
import { countTemplateLogos } from "@/lib/editor/logos";
import { draftRevisions, readEditorDraft, writeEditorDraft, type EditorDraft } from "@/lib/editor/draftStorage";
import { templateTokens } from "@/lib/editor/tokens";

import { useComposedDeck } from "./useComposedDeck";
import type { GenerationSession } from "./useGenerationSession";

/** Снимок черновика для истории: правки, порядок слайдов и слайд, на котором их сделали. */
interface DraftSnapshot {
  drafts: Record<string, Override[]>;
  order: string[] | null;
  slideIndex: number;
  logo: boolean | null;
}

/** Подряд идущие правки одного поля (набор текста) складываются в один шаг истории. */
const COALESCE_MS = 800;
const HISTORY_MAX = 100;

export interface SlideEditorOptions {
  profile: TemplateProfile | null | undefined;
  pkg: ContentPackage | null | undefined;
  /** Патч поставлен в очередь: чат добавляет карточку хода и результата. */
  onPatchStarted?: (patchJobId: string, slideIndex: number) => void;
}

/**
 * Состояние визуального редактора: описание колоды просматриваемой ревизии, черновик правок
 * по слайдам (списки overrides, как в контракте slide_patch), порядок слайдов, выбранный
 * объект, применение одной ревизией. Локальная копия привязана к заданию, варианту и
 * просматриваемой ревизии; старый черновик никогда не переносится на новую автоматически.
 */
export function useSlideEditor(session: GenerationSession, options: SlideEditorOptions) {
  const { jobId, variant, currentRevision, viewRevision, slideIndex } = session;
  const deckState = useComposedDeck(jobId, variant?.composed_deck_artifact, currentRevision, viewRevision);
  const deck = deckState.deck;
  const key = jobId && variant ? `${jobId}:${variant.variant_id}:${viewRevision}` : null;

  const [editing, setEditingRaw] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, Override[]>>({});
  const [order, setOrder] = useState<string[] | null>(null);
  const [selectedObjectId, setSelectedObjectId] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  // Адреса своих картинок черновика (object URL по file_id) — для холста до применения.
  const [fileUrls, setFileUrls] = useState<Record<string, string>>({});
  // История черновика: снимки до каждого шага и отменённые шаги для возврата.
  const [past, setPast] = useState<DraftSnapshot[]>([]);
  const [future, setFuture] = useState<DraftSnapshot[]>([]);
  const lastMark = useRef<{ key: string; at: number } | null>(null);
  // Снят ли знак шаблона в черновике: null — как в ревизии.
  const [logoDraft, setLogoDraft] = useState<boolean | null>(null);
  // Номер слайда, который надо выбрать, когда придёт ревизия с применёнными правками: после
  // перестановки слайд, который правили, получает в новой ревизии номер своего места.
  const [pending, setPending] = useState<EditorDraft["pending"]>(null);
  const [storageOk, setStorageOk] = useState(true);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedRevisions, setSavedRevisions] = useState<number[]>([]);
  const submitting = useRef(false);
  const activeKey = useRef(key);
  useEffect(() => { activeKey.current = key; }, [key]);
  const submitted = useRef<{ key: string; jobId: string; variantId: string; revision: number; pending: NonNullable<EditorDraft["pending"]> } | null>(null);

  useEffect(() => {
    const sent = submitted.current;
    if (!sent || session.editStatus?.job_id !== sent.pending.jobId || session.editStatus.status !== "succeeded") return;
    writeEditorDraft(sent.key, null);
    if (jobId === sent.jobId && variant?.variant_id === sent.variantId && currentRevision > sent.revision) {
      session.selectSlide(sent.pending.position);
      submitted.current = null;
    }
  }, [session, jobId, variant?.variant_id, currentRevision]);

  // Восстанавливаем только точный адрес. История отмены остаётся в текущем сеансе.
  const [prevKey, setPrevKey] = useState<string | null>(null);
  if (prevKey !== key) {
    const saved = key ? readEditorDraft(key) : null;
    setPrevKey(key);
    setDrafts(saved?.drafts ?? {});
    setOrder(saved?.order ?? null);
    setSelectedObjectId(null);
    setPast([]);
    setFuture([]);
    setLogoDraft(saved?.logo ?? null);
    setPending(saved?.pending ?? null);
    setSaveError(null);
    setSavedRevisions(jobId && variant ? draftRevisions(jobId, variant.variant_id) : []);
  }
  useEffect(() => {
    if (!jobId || !variant) return;
    let alive = true;
    for (const revision of savedRevisions.filter((r) => r < currentRevision)) {
      const oldKey = `${jobId}:${variant.variant_id}:${revision}`;
      const old = readEditorDraft(oldKey);
      if (!old?.pending) continue;
      void api.jobs.get(old.pending.jobId).then((status) => {
        if (!alive || status.status !== "succeeded") return;
        writeEditorDraft(oldKey, null);
        setSavedRevisions((revisions) => revisions.filter((r) => r !== revision));
      }).catch(() => { /* Неизвестный результат не повод удалять черновик. */ });
    }
    return () => { alive = false; };
  }, [jobId, variant, currentRevision, savedRevisions]);
  useEffect(() => {
    if (!pending || !key) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const status = await api.jobs.get(pending.jobId);
        if (!alive) return;
        if (status.status === "succeeded") {
          writeEditorDraft(key, null);
          setDrafts({});
          setOrder(null);
          setLogoDraft(null);
          setPast([]);
          setFuture([]);
          setPending(null);
          setSaveError(null);
          session.selectSlide(pending.position);
          session.job.refresh();
          return;
        }
        if (status.status === "failed" || status.status === "canceled") {
          setPending(null);
          setSaveError(status.error?.message ?? "Правки не применены. Черновик сохранён; можно повторить.");
          return;
        }
      } catch {
        if (!alive) return;
        setSaveError("Не удалось проверить применение. Черновик сохранён; проверка повторяется.");
      }
      timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => { alive = false; clearTimeout(timer); };
    // refresh is intentionally excluded: it changes on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, pending]);
  // Смена слайда снимает выделение объекта.
  const [prevSlide, setPrevSlide] = useState(slideIndex);
  if (prevSlide !== slideIndex) {
    setPrevSlide(slideIndex);
    setSelectedObjectId(null);
  }

  const available = Boolean(
    deck && variant && !pending && !applying && !session.busy && !session.editJob && !session.repairJob && session.terminal && viewRevision === currentRevision && (variant.status === "ready" || variant.status === "needs_review"),
  );

  const deckOrder = useMemo(() => (deck ? deck.slides.map((s) => s.slide_id) : []), [deck]);
  const slideOrder = order ?? deckOrder;
  const orderChanged = order !== null && order.join("|") !== deckOrder.join("|");
  const slidesById = useMemo(() => new Map((deck?.slides ?? []).map((s) => [s.slide_id, s])), [deck]);
  const slides: DeckSlide[] = useMemo(() => slideOrder.map((id) => slidesById.get(id)).filter((s): s is DeckSlide => Boolean(s)), [slideOrder, slidesById]);
  // Выбор слайда приходит номером в колоде: место в ленте после перестановки другое.
  const currentSlide =
    slides.find((s) => s.index === slideIndex) ?? slides[Math.min(slideIndex, Math.max(slides.length - 1, 0))] ?? null;
  const draft = currentSlide ? (drafts[currentSlide.slide_id] ?? currentSlide.overrides ?? []) : [];

  const templateId = deck?.template_id ?? options.profile?.template_id ?? null;
  const resolver: AssetResolver = useMemo(
    () => ({
      sourceUrl: (source) => {
        if (source.kind === "template" && source.asset_id && templateId) return api.templates.mediaUrl(templateId, source.asset_id);
        if (source.kind === "package" && source.asset_id && options.pkg) {
          const asset = options.pkg.assets.find((a) => a.asset_id === source.asset_id);
          return asset ? api.content.assetUrl(options.pkg.package_id, asset.path) : null;
        }
        if (source.kind === "file" && source.file_id) return fileUrls[source.file_id] ?? null;
        return null;
      },
    }),
    [templateId, options.pkg, fileUrls],
  );
  const previewSlide = currentSlide ? applyOverrides(currentSlide, draft, resolver) : null;

  const changedSlides = useMemo(() => (deck ? patchSlides(drafts, deck) : []), [drafts, deck]);
  const logoChanged = logoDraft !== null && logoDraft !== (deck?.template_logo === "drop");
  const dirty = changedSlides.length > 0 || orderChanged || logoChanged;
  const draftCount =
    changedSlides.reduce((n, s) => n + s.overrides.length, 0) +
    (orderChanged ? 1 : 0) +
    (logoChanged ? 1 : 0);

  useEffect(() => {
    if (!key || !deck) return;
    const ok = writeEditorDraft(key, dirty || pending ? { drafts, order, logo: logoDraft, pending } : null);
    // Результат синхронизации с внешним хранилищем нужен для честного статуса в UI.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setStorageOk(ok);
  }, [key, deck, dirty, drafts, order, logoDraft, pending]);

  useEffect(() => {
    if (!dirty && !pending) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, pending]);

  useEffect(() => {
    session.setEditorDirty(dirty || Boolean(pending));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirty, pending]);

  const setEditing = useCallback(
    (value: boolean) => {
      setEditingRaw(value);
      if (value) {
        session.setLayout("single");
      } else {
        setSelectedObjectId(null);
      }
    },
    [session],
  );

  /**
   * Шаг черновика с записью в историю. `mark` склеивает подряд идущие правки одного поля:
   * набор текста иначе разбирался бы обратно по букве на каждый Ctrl+Z.
   */
  const commit = useCallback(
    (next: (cur: DraftSnapshot) => Pick<DraftSnapshot, "drafts" | "order" | "logo">, mark?: string) => {
      if (!available || submitting.current) return;
      const now = Date.now();
      // Метка привязана к ключу черновика: правка в другом задании или ревизии не склеится с
      // прежней, и сбрасывать метку при смене ключа не нужно.
      const marked = mark ? `${key}|${mark}` : null;
      const glued = Boolean(marked) && lastMark.current?.key === marked && now - (lastMark.current?.at ?? 0) < COALESCE_MS;
      if (!glued) setPast((p) => [...p, { drafts, order, slideIndex, logo: logoDraft }].slice(-HISTORY_MAX));
      lastMark.current = marked ? { key: marked, at: now } : null;
      setFuture([]);
      const result = next({ drafts, order, slideIndex, logo: logoDraft });
      setDrafts(result.drafts);
      setOrder(result.order);
      setLogoDraft(result.logo);
      setSaveError(null);
    },
    [drafts, order, slideIndex, key, logoDraft, available],
  );

  const setOp = useCallback(
    (op: Override) => {
      if (!currentSlide) return;
      const id = currentSlide.slide_id;
      const mark = `${op.op}:${id}:${op.target?.object_id ?? "slide"}`;
      commit((cur) => ({ ...cur, drafts: { ...cur.drafts, [id]: mergeDraft(cur.drafts[id] ?? currentSlide.overrides ?? [], op) } }), mark);
    },
    [currentSlide, commit],
  );

  const resetObject = useCallback(
    (objectId: string | null) => {
      if (!currentSlide) return;
      const id = currentSlide.slide_id;
      commit((cur) => ({ ...cur, drafts: { ...cur.drafts, [id]: resetObjectOverrides(cur.drafts[id] ?? currentSlide.overrides ?? [], objectId) } }));
    },
    [currentSlide, commit],
  );

  const discard = useCallback(() => {
    if (viewRevision !== currentRevision && !pending && !applying) {
      setDrafts({});
      setOrder(null);
      setLogoDraft(null);
      if (key) writeEditorDraft(key, null);
      return;
    }
    commit(() => ({ drafts: {}, order: null, logo: null }));
  }, [commit, viewRevision, currentRevision, pending, applying, key]);

  /** Шаг назад: возвращается и слайд, на котором правка была сделана, — иначе непонятно, что изменилось. */
  const undo = useCallback(() => {
    if (!available || past.length === 0) return;
    const prev = past[past.length - 1];
    setPast(past.slice(0, -1));
    setFuture([{ drafts, order, slideIndex, logo: logoDraft }, ...future].slice(0, HISTORY_MAX));
    setDrafts(prev.drafts);
    setOrder(prev.order);
    setLogoDraft(prev.logo);
    lastMark.current = null;
    if (prev.slideIndex !== slideIndex) session.selectSlide(prev.slideIndex);
  }, [past, future, drafts, order, slideIndex, session, available, logoDraft]);

  const redo = useCallback(() => {
    if (!available || future.length === 0) return;
    const next = future[0];
    setFuture(future.slice(1));
    setPast([...past, { drafts, order, slideIndex, logo: logoDraft }].slice(-HISTORY_MAX));
    setDrafts(next.drafts);
    setOrder(next.order);
    setLogoDraft(next.logo);
    lastMark.current = null;
    if (next.slideIndex !== slideIndex) session.selectSlide(next.slideIndex);
  }, [past, future, drafts, order, slideIndex, session, available, logoDraft]);

  // Ctrl+Z и Ctrl+Shift+Z (Ctrl+Y) на всей странице редактора. В полях ввода не перехватываем:
  // там работает своя отмена ввода браузера.
  useEffect(() => {
    if (!available) return;
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      const key = e.key.toLowerCase();
      if (key !== "z" && key !== "y") return;
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return;
      e.preventDefault();
      if (key === "y" || e.shiftKey) redo();
      else undo();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [available, undo, redo]);

  const reorder = useCallback(
    (from: number, to: number) => {
      if (from === to || from < 0 || to < 0 || from >= slideOrder.length || to >= slideOrder.length) return;
      const next = [...slideOrder];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      commit((cur) => ({ ...cur, order: next.join("|") === deckOrder.join("|") ? null : next }));
      // Выбранным остаётся тот же слайд, который перетащили.
      const movedSlide = slidesById.get(moved);
      if (movedSlide) session.selectSlide(movedSlide.index);
    },
    [slideOrder, deckOrder, session, slidesById, commit],
  );

  const registerFileUrl = useCallback((fileId: string, url: string) => {
    setFileUrls((m) => ({ ...m, [fileId]: url }));
  }, []);

  const apply = useCallback(async () => {
    if (!deck || !jobId || !variant || !dirty || !available || submitting.current) return;
    submitting.current = true;
    setApplying(true);
    setSaveError(null);
    try {
      const patchJobId = await session.requestPatch(
        { jobId, variantId: variant.variant_id, revision: currentRevision },
        changedSlides,
        orderChanged ? slideOrder : undefined,
        logoChanged ? (logoDraft ? "drop" : "keep") : undefined,
      );
      // Новая ревизия нумерует слайды по применённому порядку: номер изменённого слайда в
      // ней — его место в черновом порядке, а не прежний номер в колоде.
      const firstChanged = changedSlides[0] ? slideOrder.indexOf(changedSlides[0].slide_id) : slideIndex;
      const nextPending = { jobId: patchJobId, position: currentSlide ? Math.max(0, slideOrder.indexOf(currentSlide.slide_id)) : 0 };
      // Записываем адрес принятого задания до следующего рендера/перехода.
      if (key) writeEditorDraft(key, { drafts, order, logo: logoDraft, pending: nextPending });
      if (key) submitted.current = { key, jobId, variantId: variant.variant_id, revision: currentRevision, pending: nextPending };
      if (activeKey.current === key) setPending(nextPending);
      options.onPatchStarted?.(patchJobId, Math.max(firstChanged, 0));
    } catch (e) {
      const code = e instanceof ApiError ? e.code : "";
      const message = e instanceof ApiError ? e.message : "неизвестная ошибка";
      if (activeKey.current === key) setSaveError(message);
      if (code === "revision_stale") notifications.show({ color: "orange", title: "Ревизия устарела", message: "Черновик сохранён у исходной ревизии. Он не будет автоматически наложен на новую.", autoClose: 8000 });
      else if (code === "repair_in_progress") notifications.show({ color: "orange", title: "Предыдущая правка ещё применяется", message: "Дождитесь её и нажмите «Применить» снова." });
      else notifications.show({ color: "red", title: "Правки не отправлены", message });
    } finally {
      submitting.current = false;
      setApplying(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deck, jobId, variant, dirty, available, session, currentRevision, changedSlides, orderChanged, slideOrder, slideIndex, currentSlide, options.onPatchStarted, key, drafts, order, logoDraft, logoChanged]);

  const tokens = useMemo(() => templateTokens(options.profile), [options.profile]);

  // Знак шаблона: он лежит на макетах, а не на слайдах, поэтому правкой объекта его не снять —
  // это свойство всей колоды (`template_logo` в плане). Профиль знает, есть ли он вообще.
  const logoCount = useMemo(() => countTemplateLogos(options.profile), [options.profile]);
  const logoDropped = logoDraft ?? deck?.template_logo === "drop";

  /** Снимает знак шаблона со всей колоды или возвращает его: шаг черновика, как и остальные. */
  const toggleLogo = useCallback(() => {
    if (logoCount === 0) return;
    commit((cur) => ({ ...cur, logo: !logoDropped }));
  }, [logoCount, logoDropped, commit]);

  /**
   * Своя надпись на текущем слайде: рамка по центру со смещением, чтобы несколько надписей
   * не ложились одна на другую, кегль и гарнитура — из токенов шаблона. Адрес придуман здесь
   * (`usr_…`), композер выведет из него номер фигуры.
   */
  const addText = useCallback(() => {
    if (!currentSlide) return;
    const slideId = currentSlide.slide_id;
    const objectId = `${NEW_TEXT_PREFIX}${Date.now().toString(36)}`;
    const size = tokens.sizes.length > 0 ? tokens.sizes[Math.floor(tokens.sizes.length / 2)] : 18;
    const family = tokens.families[0];
    commit((cur) => {
      const base = cur.drafts[slideId] ?? currentSlide.overrides ?? [];
      const step = Math.min(base.filter((o) => o.op === "add_text").length, 6) * 0.03;
      const op: Override = {
        op: "add_text",
        target: { object_id: objectId },
        text: "Новый текст",
        geometry: { bbox: { x: 0.28 + step, y: 0.4 + step, width: 0.44, height: 0.12 } },
        style: { font: { size_pt: size, ...(family ? { family } : {}) } },
      };
      return { ...cur, drafts: { ...cur.drafts, [slideId]: mergeDraft(base, op) } };
    });
    setSelectedObjectId(objectId);
  }, [currentSlide, tokens, commit]);

  /**
   * Объект убирается со слайда. Свою надпись достаточно вычеркнуть из черновика — на сервере
   * её ещё нет; у объекта колоды вместе с правкой `delete` снимаются и прежние правки на него,
   * иначе композер искал бы то, чего уже не будет.
   */
  const deleteObject = useCallback(
    (objectId: string) => {
      if (!currentSlide || !objectId) return;
      const slideId = currentSlide.slide_id;
      const source = currentSlide.objects.find((o) => o.object_id === objectId)?.source_object_id;
      commit((cur) => {
        const base = cur.drafts[slideId] ?? currentSlide.overrides ?? [];
        const cleaned = resetObjectOverrides(base, objectId);
        const next = isNewText(objectId)
          ? cleaned
          : mergeDraft(cleaned, {
              op: "delete",
              target: { object_id: objectId, ...(source ? { source_object_id: source } : {}) },
            });
        return { ...cur, drafts: { ...cur.drafts, [slideId]: next } };
      });
      setSelectedObjectId(null);
    },
    [currentSlide, commit],
  );

  const summary = currentSlide ? describeDraft(draft, currentSlide) : "";
  const slideDirty = currentSlide ? !sameOverrides(draft, currentSlide.overrides ?? []) : false;

  return {
    editing: editing && viewRevision === currentRevision,
    setEditing,
    available,
    deck,
    deckError: deckState.error,
    deckLoading: deckState.loading,
    slides,
    slideOrder,
    orderChanged,
    currentSlide,
    previewSlide,
    draft,
    drafts,
    slideDirty,
    dirty,
    draftCount,
    changedSlides,
    summary,
    selectedObjectId,
    selectObject: setSelectedObjectId,
    setOp,
    addText,
    deleteObject,
    toggleLogo,
    logoCount,
    logoDropped,
    resetObject,
    discard,
    reorder,
    undo,
    redo,
    canUndo: available && past.length > 0,
    canRedo: available && future.length > 0,
    apply,
    applying: applying || Boolean(pending) || (Boolean(session.editJob) && session.editJobKind === "patch"),
    storageOk,
    saveError,
    savedRevisions: savedRevisions.filter((r) => r < currentRevision),
    missingFilePreview: Object.values(drafts).flat().some((op) => {
      const source = op.picture?.source ?? op.background?.source;
      return source?.kind === "file" && source.file_id && !fileUrls[source.file_id];
    }),
    tokens,
    resolver,
    registerFileUrl,
    templateId,
  };
}

export type SlideEditor = ReturnType<typeof useSlideEditor>;
