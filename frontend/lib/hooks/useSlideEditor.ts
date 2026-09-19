"use client";

import { notifications } from "@mantine/notifications";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api, ApiError } from "@/lib/api/client";
import type { ContentPackage, Override, TemplateProfile } from "@/lib/api/types";
import {
  applyOverrides,
  describeDraft,
  mergeDraft,
  patchSlides,
  resetObject as resetObjectOverrides,
  sameOverrides,
  type AssetResolver,
  type DeckSlide,
} from "@/lib/editor/overrides";
import { templateTokens } from "@/lib/editor/tokens";

import { useComposedDeck } from "./useComposedDeck";
import type { GenerationSession } from "./useGenerationSession";

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

  // Новый ключ (задание, вариант, ревизия) — черновик отброшен: применённые правки уже в эхе.
  const [prevKey, setPrevKey] = useState(key);
  if (prevKey !== key) {
    setPrevKey(key);
    setDrafts({});
    setOrder(null);
    setSelectedObjectId(null);
  }
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
  const currentSlide = slides[Math.min(slideIndex, Math.max(slides.length - 1, 0))] ?? null;
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

  const setOp = useCallback(
    (op: Override) => {
      if (!currentSlide) return;
      const id = currentSlide.slide_id;
      setDrafts((d) => ({ ...d, [id]: mergeDraft(d[id] ?? currentSlide.overrides ?? [], op) }));
    },
    [currentSlide],
  );

  const resetObject = useCallback(
    (objectId: string | null) => {
      if (!currentSlide) return;
      const id = currentSlide.slide_id;
      setDrafts((d) => ({ ...d, [id]: resetObjectOverrides(d[id] ?? currentSlide.overrides ?? [], objectId) }));
    },
    [currentSlide],
  );

  const discard = useCallback(() => {
    setDrafts({});
    setOrder(null);
  }, []);

  const reorder = useCallback(
    (from: number, to: number) => {
      if (from === to || from < 0 || to < 0 || from >= slideOrder.length || to >= slideOrder.length) return;
      const next = [...slideOrder];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      setOrder(next.join("|") === deckOrder.join("|") ? null : next);
      session.selectSlide(to);
    },
    [slideOrder, deckOrder, session],
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
      const firstChanged = changedSlides[0] ? slideOrder.indexOf(changedSlides[0].slide_id) : slideIndex;
      options.onPatchStarted?.(patchJobId, Math.max(firstChanged, 0));
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
  }, [deck, jobId, variant, dirty, applying, session, currentRevision, changedSlides, orderChanged, slideOrder, slideIndex, options.onPatchStarted]);

  const tokens = useMemo(() => templateTokens(options.profile), [options.profile]);
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
    resetObject,
    discard,
    reorder,
    apply,
    applying: applying || (Boolean(session.editJob) && session.editJobKind === "patch"),
    tokens,
    resolver,
    registerFileUrl,
    templateId,
  };
}

export type SlideEditor = ReturnType<typeof useSlideEditor>;
