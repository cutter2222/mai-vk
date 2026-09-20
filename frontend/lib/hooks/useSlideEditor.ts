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
import { templateTokens } from "@/lib/editor/tokens";

import { useComposedDeck } from "./useComposedDeck";
import type { GenerationSession } from "./useGenerationSession";

/** Снимок черновика для истории: правки, порядок слайдов и слайд, на котором их сделали. */
interface DraftSnapshot {
  drafts: Record<string, Override[]>;
  order: string[] | null;
  slideIndex: number;
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
 * объект, применение одной ревизией. Черновик живёт только в этой вкладке и сбрасывается,
 * когда меняется задание, вариант или появляется новая ревизия: тогда правки уже в эхе.
 */
export function useSlideEditor(session: GenerationSession, options: SlideEditorOptions) {
  const { jobId, variant, currentRevision, viewRevision, slideIndex } = session;
  const deckState = useComposedDeck(jobId, variant?.composed_deck_artifact, currentRevision, viewRevision);
  const deck = deckState.deck;
  const key = jobId && variant ? `${jobId}:${variant.variant_id}:${currentRevision}` : null;

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
  // Номер слайда, который надо выбрать, когда придёт ревизия с применёнными правками: после
  // перестановки слайд, который правили, получает в новой ревизии номер своего места.
  const pendingSelect = useRef<number | null>(null);

  // Новый ключ (задание, вариант, ревизия) — черновик отброшен: применённые правки уже в эхе.
  const [prevKey, setPrevKey] = useState(key);
  if (prevKey !== key) {
    setPrevKey(key);
    setDrafts({});
    setOrder(null);
    setSelectedObjectId(null);
    setPast([]);
    setFuture([]);
  }
  useEffect(() => {
    const position = pendingSelect.current;
    if (position === null) return;
    pendingSelect.current = null;
    if (position >= 0 && position !== session.slideIndex) session.selectSlide(position);
  }, [key, session]);
  // Смена слайда снимает выделение объекта.
  const [prevSlide, setPrevSlide] = useState(slideIndex);
  if (prevSlide !== slideIndex) {
    setPrevSlide(slideIndex);
    setSelectedObjectId(null);
  }

  const available = Boolean(
    deck && variant && session.terminal && viewRevision === currentRevision && (variant.status === "ready" || variant.status === "needs_review"),
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
  const dirty = changedSlides.length > 0 || orderChanged;
  const draftCount = changedSlides.reduce((n, s) => n + s.overrides.length, 0) + (orderChanged ? 1 : 0);

  useEffect(() => {
    session.setEditorDirty(dirty);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirty]);

  const setEditing = useCallback(
    (value: boolean) => {
      setEditingRaw(value);
      if (value) {
        session.setLayout("single");
        session.setAuditOpen(false);
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
    (next: (cur: DraftSnapshot) => { drafts: Record<string, Override[]>; order: string[] | null }, mark?: string) => {
      const now = Date.now();
      // Метка привязана к ключу черновика: правка в другом задании или ревизии не склеится с
      // прежней, и сбрасывать метку при смене ключа не нужно.
      const marked = mark ? `${key}|${mark}` : null;
      const glued = Boolean(marked) && lastMark.current?.key === marked && now - (lastMark.current?.at ?? 0) < COALESCE_MS;
      if (!glued) setPast((p) => [...p, { drafts, order, slideIndex }].slice(-HISTORY_MAX));
      lastMark.current = marked ? { key: marked, at: now } : null;
      setFuture([]);
      const result = next({ drafts, order, slideIndex });
      setDrafts(result.drafts);
      setOrder(result.order);
    },
    [drafts, order, slideIndex, key],
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
    commit(() => ({ drafts: {}, order: null }));
  }, [commit]);

  /** Шаг назад: возвращается и слайд, на котором правка была сделана, — иначе непонятно, что изменилось. */
  const undo = useCallback(() => {
    if (past.length === 0) return;
    const prev = past[past.length - 1];
    setPast(past.slice(0, -1));
    setFuture([{ drafts, order, slideIndex }, ...future].slice(0, HISTORY_MAX));
    setDrafts(prev.drafts);
    setOrder(prev.order);
    lastMark.current = null;
    if (prev.slideIndex !== slideIndex) session.selectSlide(prev.slideIndex);
  }, [past, future, drafts, order, slideIndex, session]);

  const redo = useCallback(() => {
    if (future.length === 0) return;
    const next = future[0];
    setFuture(future.slice(1));
    setPast([...past, { drafts, order, slideIndex }].slice(-HISTORY_MAX));
    setDrafts(next.drafts);
    setOrder(next.order);
    lastMark.current = null;
    if (next.slideIndex !== slideIndex) session.selectSlide(next.slideIndex);
  }, [past, future, drafts, order, slideIndex, session]);

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
    if (!deck || !jobId || !variant || !dirty || applying) return;
    setApplying(true);
    try {
      const patchJobId = await session.requestPatch(
        { jobId, variantId: variant.variant_id, revision: currentRevision },
        changedSlides,
        orderChanged ? slideOrder : undefined,
      );
      // Новая ревизия нумерует слайды по применённому порядку: номер изменённого слайда в
      // ней — его место в черновом порядке, а не прежний номер в колоде.
      const firstChanged = changedSlides[0] ? slideOrder.indexOf(changedSlides[0].slide_id) : slideIndex;
      options.onPatchStarted?.(patchJobId, Math.max(firstChanged, 0));
      pendingSelect.current = currentSlide ? slideOrder.indexOf(currentSlide.slide_id) : null;
    } catch (e) {
      const code = e instanceof ApiError ? e.code : "";
      const message = e instanceof ApiError ? e.message : "неизвестная ошибка";
      if (code === "revision_stale") notifications.show({ color: "orange", title: "Ревизия устарела", message: "Справа уже новая ревизия: черновик сброшен, повторите правки на ней.", autoClose: 8000 });
      else if (code === "repair_in_progress") notifications.show({ color: "orange", title: "Предыдущая правка ещё применяется", message: "Дождитесь её и нажмите «Применить» снова." });
      else notifications.show({ color: "red", title: "Правки не отправлены", message });
    } finally {
      setApplying(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deck, jobId, variant, dirty, applying, session, currentRevision, changedSlides, orderChanged, slideOrder, slideIndex, currentSlide, options.onPatchStarted]);

  const tokens = useMemo(() => templateTokens(options.profile), [options.profile]);

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
    editing,
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
    resetObject,
    discard,
    reorder,
    undo,
    redo,
    canUndo: past.length > 0,
    canRedo: future.length > 0,
    apply,
    applying: applying || (Boolean(session.editJob) && session.editJobKind === "patch"),
    tokens,
    resolver,
    registerFileUrl,
    templateId,
  };
}

export type SlideEditor = ReturnType<typeof useSlideEditor>;
