"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { ComposedDeck } from "@/lib/api/types";
import type { DeckObject, DeckSlide } from "@/lib/editor/overrides";

const EMU_PER_PT = 12700;
const NUDGE = 0.005;
const NUDGE_BIG = 0.02;
const MIN_SIZE = 0.01;

export interface CanvasBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface Props {
  deck: ComposedDeck;
  slide: DeckSlide;
  /** Подложка: пустой слайд макета (фон, декор, логотипы), которых нет среди объектов. */
  layoutUrl: string | null;
  /** Миниатюра ревизии — вырезки для таблиц, диаграмм и прочих объектов, которых холст не рисует сам. */
  thumbUrl: string | undefined;
  /** Адрес байтов ресурса колоды по asset_id (медиа ревизии). */
  mediaUrl: (assetId: string) => string | undefined;
  selectedObjectId: string | null;
  onSelect: (objectId: string | null) => void;
  /** Перетаскивание и ручки включены; иначе холст только показывает и выделяет. */
  editable: boolean;
  onGeometry?: (objectId: string, bbox: CanvasBox) => void;
  /** Тон подложки, если превью макета нет: цвет фона. */
  fallbackBackground?: string;
}

type DragState =
  | { kind: "move"; id: string; startX: number; startY: number; box: CanvasBox; current: CanvasBox; moved: boolean }
  | { kind: "resize"; id: string; handle: string; startX: number; startY: number; box: CanvasBox; current: CanvasBox; moved: boolean };

const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"] as const;
const CROP_KINDS = new Set(["table", "chart", "other", "connector"]);

/**
 * Живой холст слайда из объектов ComposedDeck: подложка макета, фон, объекты по z-order.
 * Текст — кегль в cqw от ширины слайда (как в HTML-экспорте), картинки — медиа ревизии,
 * фигуры — заливка и линия; таблицы и диаграммы — вырезка из миниатюры. Клик выделяет объект,
 * перетаскивание и ручки меняют положение и размер в долях слайда, стрелки двигают на шаг.
 */
export function SlideCanvas({ deck, slide, layoutUrl, thumbUrl, mediaUrl, selectedObjectId, onSelect, editable, onGeometry, fallbackBackground }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  const widthPt = (deck.slide_size.width_emu || 12192000) / EMU_PER_PT;
  const ratio = (deck.slide_size.width_emu || 12192000) / (deck.slide_size.height_emu || 6858000);
  const background = slide.background ?? { kind: "inherited" };
  const bgColor = background.kind === "solid" && background.color ? background.color : undefined;
  const draftBg = (slide as DeckSlide & { _draft_bg_url?: string | null })._draft_bg_url;
  const bgImage = background.kind === "image" ? (draftBg !== undefined ? (draftBg ?? undefined) : background.asset_id ? mediaUrl(background.asset_id) : undefined) : undefined;
  const showLayout = background.kind === "inherited" && Boolean(layoutUrl);

  const toFraction = useCallback((dx: number, dy: number): [number, number] => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect || rect.width === 0 || rect.height === 0) return [0, 0];
    return [dx / rect.width, dy / rect.height];
  }, []);

  const startMove = (e: React.PointerEvent, obj: DeckObject) => {
    if (e.button !== 0) return;
    onSelect(obj.object_id);
    if (!editable || !movable(obj)) return;
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    setDrag({ kind: "move", id: obj.object_id, startX: e.clientX, startY: e.clientY, box: { ...obj.bbox }, current: { ...obj.bbox }, moved: false });
  };

  const startResize = (e: React.PointerEvent, obj: DeckObject, handle: string) => {
    if (e.button !== 0 || !editable) return;
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    setDrag({ kind: "resize", id: obj.object_id, handle, startX: e.clientX, startY: e.clientY, box: { ...obj.bbox }, current: { ...obj.bbox }, moved: false });
  };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const [dx, dy] = toFraction(e.clientX - drag.startX, e.clientY - drag.startY);
    const moved = drag.moved || Math.abs(e.clientX - drag.startX) > 2 || Math.abs(e.clientY - drag.startY) > 2;
    let current: CanvasBox;
    if (drag.kind === "move") {
      current = { ...drag.box, x: round(drag.box.x + dx), y: round(drag.box.y + dy) };
    } else {
      current = resizeBox(drag.box, drag.handle, dx, dy);
    }
    setDrag({ ...drag, current, moved });
  };

  const onPointerUp = () => {
    if (!drag) return;
    if (drag.moved && onGeometry) onGeometry(drag.id, drag.current);
    setDrag(null);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      onSelect(null);
      e.stopPropagation();
      return;
    }
    if (!editable || !selectedObjectId) return;
    const obj = slide.objects.find((o) => o.object_id === selectedObjectId);
    if (!obj || !movable(obj)) return;
    const step = e.shiftKey ? NUDGE_BIG : NUDGE;
    const delta: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const d = delta[e.key];
    if (!d) return;
    e.preventDefault();
    e.stopPropagation();
    onGeometry?.(obj.object_id, { ...obj.bbox, x: round(obj.bbox.x + d[0]), y: round(obj.bbox.y + d[1]) });
  };

  useEffect(() => {
    if (selectedObjectId && ref.current) ref.current.focus({ preventScroll: true });
  }, [selectedObjectId]);

  const objects = [...slide.objects].filter((o) => o.kind !== "group").sort((a, b) => a.z_order - b.z_order);
  const selected = objects.find((o) => o.object_id === selectedObjectId);
  const selectedBox = drag && selected && drag.id === selected.object_id ? drag.current : selected?.bbox;

  return (
    <div
      ref={ref}
      className="slide-canvas"
      data-testid="slide-canvas"
      data-editable={editable}
      tabIndex={0}
      style={{ aspectRatio: `${ratio}`, background: bgColor ?? (showLayout ? "#fff" : (fallbackBackground ?? "#fff")) }}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onKeyDown={onKeyDown}
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onSelect(null);
      }}
    >
      {showLayout && layoutUrl && (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="canvas-layer" src={layoutUrl} alt="" draggable={false} />
      )}
      {bgImage && (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="canvas-layer" src={bgImage} alt="" draggable={false} style={{ objectFit: "cover" }} />
      )}
      {objects.map((obj) => {
        const box = drag && drag.id === obj.object_id ? drag.current : obj.bbox;
        return (
          <CanvasObject
            key={obj.object_id}
            obj={obj}
            box={box}
            widthPt={widthPt}
            thumbUrl={thumbUrl}
            mediaUrl={mediaUrl}
            selected={obj.object_id === selectedObjectId}
            onPointerDown={(e) => startMove(e, obj)}
          />
        );
      })}
      {editable && selected && selectedBox && movable(selected) && (
        <div className="canvas-selection" style={percentBox(selectedBox)} data-testid="canvas-selection">
          {HANDLES.map((h) => (
            <div key={h} className="canvas-handle" data-handle={h} onPointerDown={(e) => startResize(e, selected, h)} />
          ))}
        </div>
      )}
    </div>
  );
}

function movable(obj: DeckObject): boolean {
  return obj.kind !== "connector" && obj.kind !== "other";
}

function round(v: number): number {
  return Math.round(v * 10000) / 10000;
}

function resizeBox(box: CanvasBox, handle: string, dx: number, dy: number): CanvasBox {
  let { x, y, width, height } = box;
  if (handle.includes("e")) width = Math.max(MIN_SIZE, box.width + dx);
  if (handle.includes("s")) height = Math.max(MIN_SIZE, box.height + dy);
  if (handle.includes("w")) {
    const w = Math.max(MIN_SIZE, box.width - dx);
    x = box.x + (box.width - w);
    width = w;
  }
  if (handle.includes("n")) {
    const h = Math.max(MIN_SIZE, box.height - dy);
    y = box.y + (box.height - h);
    height = h;
  }
  return { x: round(x), y: round(y), width: round(width), height: round(height) };
}

export function percentBox(box: CanvasBox): React.CSSProperties {
  return { left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.width * 100}%`, height: `${box.height * 100}%` };
}

interface ObjectProps {
  obj: DeckObject;
  box: CanvasBox;
  widthPt: number;
  thumbUrl: string | undefined;
  mediaUrl: (assetId: string) => string | undefined;
  selected: boolean;
  onPointerDown: (e: React.PointerEvent) => void;
}

function CanvasObject({ obj, box, widthPt, thumbUrl, mediaUrl, selected, onPointerDown }: ObjectProps) {
  const style: React.CSSProperties = { ...percentBox(box) };
  if (obj.rotation_deg) style.transform = `rotate(${obj.rotation_deg}deg)`;
  if (obj.fill?.kind === "solid" && obj.fill.color) style.background = obj.fill.color;
  if (obj.line?.color) style.boxShadow = `inset 0 0 0 1px ${obj.line.color}`;
  if (obj.geometry === "ellipse") style.borderRadius = "50%";
  else if (obj.geometry === "roundRect") style.borderRadius = "8% / 12%";
  const common = {
    className: "canvas-object",
    "data-testid": `canvas-object-${obj.object_id}`,
    "data-kind": obj.kind,
    "data-selected": selected || undefined,
    "data-user": obj.user_overrides && obj.user_overrides.length > 0 ? true : undefined,
    onPointerDown,
    title: obj.name ?? undefined,
  } as const;

  const draft = obj as DeckObject & { _draft_url?: string | null; _draft_color?: string };
  if (obj.kind === "picture") {
    const src = draft._draft_url === undefined ? (obj.picture?.asset_id ? mediaUrl(obj.picture.asset_id) : undefined) : (draft._draft_url ?? (obj.picture?.asset_id ? mediaUrl(obj.picture.asset_id) : undefined));
    const fit = obj.picture?.fit === "contain" ? "contain" : obj.picture?.fit === "cover" ? "cover" : "fill";
    if (!src) {
      return <div {...common} style={{ ...style, background: "repeating-linear-gradient(45deg,#e9ecef,#e9ecef 6px,#f8f9fa 6px,#f8f9fa 12px)" }} />;
    }
    if (draft._draft_color) {
      return (
        <div {...common} style={style}>
          <div className="canvas-mask" style={{ WebkitMaskImage: `url(${src})`, maskImage: `url(${src})`, WebkitMaskSize: fit, maskSize: fit, background: draft._draft_color }} />
        </div>
      );
    }
    if (/\.(emf|wmf)$/i.test(src)) return <CropObject common={common} style={style} box={box} thumbUrl={thumbUrl} />;
    return (
      <div {...common} style={style}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={src} alt={obj.name ?? ""} draggable={false} style={{ objectFit: fit }} />
      </div>
    );
  }
  if (CROP_KINDS.has(obj.kind)) return <CropObject common={common} style={style} box={box} thumbUrl={thumbUrl} />;

  const paragraphs = obj.text?.paragraphs ?? (obj.text?.plain ? [{ text: obj.text.plain }] : []);
  if (paragraphs.length === 0) return <div {...common} style={style} />;
  const insets = obj.text?.insets ?? {};
  const pad = (v: number | undefined) => `${((v ?? 0) * 100).toFixed(2)}cqw`;
  const base = obj.text?.computed_style ?? {};
  return (
    <div {...common} style={{ ...style, padding: `${pad(insets.top)} ${pad(insets.right)} ${pad(insets.bottom)} ${pad(insets.left)}` }}>
      {paragraphs.map((p, i) => {
        const font = { ...(base.font ?? {}), ...(p.style?.font ?? {}) };
        const sizePt = font.size_pt ?? 18;
        const ps: React.CSSProperties = { fontSize: `${((sizePt / widthPt) * 100).toFixed(3)}cqw` };
        if (font.family) ps.fontFamily = `"${font.family}", "Play", system-ui, sans-serif`;
        if (font.color) ps.color = font.color;
        if (font.bold) ps.fontWeight = 700;
        if (font.italic) ps.fontStyle = "italic";
        if (font.line_spacing) ps.lineHeight = font.line_spacing;
        if (p.align && p.align !== "left") ps.textAlign = p.align;
        const spacing = p.style ?? base;
        if (spacing.space_before_pt) ps.marginTop = `${((spacing.space_before_pt / widthPt) * 100).toFixed(3)}cqw`;
        if (spacing.space_after_pt) ps.marginBottom = `${((spacing.space_after_pt / widthPt) * 100).toFixed(3)}cqw`;
        const bullet = p.bullet ? (spacing.bullet?.char ?? "•") : "";
        return (
          <p key={i} style={ps}>
            {bullet ? `${bullet} ` : ""}
            {p.text}
          </p>
        );
      })}
    </div>
  );
}

function CropObject({ common, style, box, thumbUrl }: { common: Record<string, unknown>; style: React.CSSProperties; box: CanvasBox; thumbUrl: string | undefined }) {
  if (!thumbUrl || box.width <= 0 || box.height <= 0) return <div {...common} style={style} className="canvas-object canvas-crop" />;
  const px = box.width >= 1 ? 0 : (box.x / (1 - box.width)) * 100;
  const py = box.height >= 1 ? 0 : (box.y / (1 - box.height)) * 100;
  return (
    <div
      {...common}
      className="canvas-object canvas-crop"
      style={{ ...style, backgroundImage: `url(${thumbUrl})`, backgroundSize: `${100 / box.width}% ${100 / box.height}%`, backgroundPosition: `${px}% ${py}%` }}
    />
  );
}
