"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { ComposedDeck } from "@/lib/api/types";
import { EDGE_PX, hitTest, isHollow } from "@/lib/editor/hit";
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
  /** Delete или Backspace на выделенном объекте. */
  onDelete?: (objectId: string) => void;
  /** Тон подложки, если превью макета нет: цвет фона. */
  fallbackBackground?: string;
}

type DragState =
  | { kind: "move"; id: string; startX: number; startY: number; box: CanvasBox; current: CanvasBox; moved: boolean }
  | { kind: "resize"; id: string; handle: string; startX: number; startY: number; box: CanvasBox; current: CanvasBox; moved: boolean };

const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"] as const;
/** Скругление roundRect по умолчанию, когда в файле нет своего «adj» (PowerPoint: 16667/100000). */
const DEFAULT_ROUND_ADJ = 0.16667;
const CROP_KINDS = new Set(["table", "chart", "other", "connector"]);

/**
 * Одинарный интервал шрифта долями кегля. В OOXML `a:lnSpc/a:spcPct` 100% — это «одинарный»
 * интервал шрифта (ascent + descent + gap), а не 1em: ставить `line-height: 1` значило делать
 * строки на четверть теснее, чем на слайде. В CSS одинарный называется `normal`, умножить его
 * нельзя, поэтому он меряется один раз на гарнитуру скрытым образцом.
 */
const NATURAL_LINE = new Map<string, number>();

function naturalLine(family: string, bold: boolean, italic: boolean): number {
  const key = `${family}|${bold}|${italic}`;
  const cached = NATURAL_LINE.get(key);
  if (cached !== undefined) return cached;
  if (typeof document === "undefined") return 1.2;
  const probe = document.createElement("div");
  probe.textContent = "Ag";
  probe.style.cssText = "position:absolute;left:-9999px;top:0;visibility:hidden;white-space:pre;line-height:normal;font-size:100px";
  probe.style.fontFamily = family ? `"${family}", "Play", system-ui, sans-serif` : "system-ui, sans-serif";
  if (bold) probe.style.fontWeight = "700";
  if (italic) probe.style.fontStyle = "italic";
  document.body.appendChild(probe);
  const ratio = probe.getBoundingClientRect().height / 100;
  probe.remove();
  if (!ratio) return 1.2;
  // Пока шрифт колоды не загрузился, мерится запасная гарнитура — такое значение не храним.
  const loaded = !family || (document.fonts?.check?.(`100px "${family}"`) ?? true);
  if (loaded) NATURAL_LINE.set(key, ratio);
  return ratio;
}

/**
 * Живой холст слайда из объектов ComposedDeck: подложка макета, фон, объекты по z-order.
 * Текст — кегль в cqw от ширины слайда (как в HTML-экспорте), картинки — медиа ревизии,
 * фигуры — заливка и линия; таблицы и диаграммы — вырезка из миниатюры. Клик выделяет объект,
 * перетаскивание и ручки меняют положение и размер в долях слайда, стрелки двигают на шаг.
 */
export function SlideCanvas({ deck, slide, layoutUrl, thumbUrl, mediaUrl, selectedObjectId, onSelect, editable, onGeometry, onDelete, fallbackBackground }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [drag, setDrag] = useState<DragState | null>(null);
  // Объект под указателем и рамка его букв: меряется в обработчике движения, а не при
  // рендере, — подсказка наведения рисуется по ней без чтения DOM во время рендера.
  const [hovered, setHovered] = useState<{ id: string; box: CanvasBox | null } | null>(null);
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

  /** Прямоугольники самих букв объекта (в долях слайда): рамка текста бывает втрое выше строки. */
  const glyphBox = useCallback((objectId: string): CanvasBox | null => {
    const root = ref.current;
    const el = root?.querySelector<HTMLElement>(`[data-testid="canvas-object-${objectId}"]`);
    if (!root || !el) return null;
    const paragraphs = [...el.querySelectorAll("p")];
    if (paragraphs.length === 0) return null;
    const canvas = root.getBoundingClientRect();
    if (!canvas.width || !canvas.height) return null;
    let left = Infinity;
    let top = Infinity;
    let right = -Infinity;
    let bottom = -Infinity;
    for (const par of paragraphs) {
      const range = document.createRange();
      range.selectNodeContents(par);
      for (const r of range.getClientRects()) {
        if (r.width < 1 || r.height < 1) continue;
        left = Math.min(left, r.left);
        top = Math.min(top, r.top);
        right = Math.max(right, r.right);
        bottom = Math.max(bottom, r.bottom);
      }
    }
    if (!Number.isFinite(left) || right <= left || bottom <= top) return null;
    return {
      x: (left - canvas.left) / canvas.width,
      y: (top - canvas.top) / canvas.height,
      width: (right - left) / canvas.width,
      height: (bottom - top) / canvas.height,
    };
  }, []);

  /**
   * Объект под точкой: сверху вниз по стопке. У текста ловят сами буквы, а не рамка вокруг
   * них: место́ рамки часто втрое больше строки, и клик рядом с текстом выделял «контейнер».
   * Мимо букв рамка ведёт себя как пустая — ловит только края и пропускает клик ниже.
   */
  const hit = (clientX: number, clientY: number): DeckObject | null => {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect || !rect.width || !rect.height) return null;
    const fx = (clientX - rect.left) / rect.width;
    const fy = (clientY - rect.top) / rect.height;
    const padX = EDGE_PX / rect.width;
    const padY = EDGE_PX / rect.height;
    // Буквы меряются только у тех объектов, чья рамка накрыла точку: на каждое движение мыши
    // обходить весь слайд незачем.
    const near = objects.filter(
      (o) => o.kind !== "group" && fx >= o.bbox.x && fx <= o.bbox.x + o.bbox.width && fy >= o.bbox.y && fy <= o.bbox.y + o.bbox.height,
    );
    const found = hitTest(
      near.map((o) => {
        const g = glyphBox(o.object_id);
        const inGlyphs = g !== null && fx >= g.x - padX && fx <= g.x + g.width + padX && fy >= g.y - padY && fy <= g.y + g.height + padY;
        return { obj: o, bbox: o.bbox, z: o.z_order, hollow: g ? !inGlyphs : isHollow(o) };
      }),
      fx,
      fy,
      padX,
      padY,
    );
    return found?.obj ?? null;
  };

  /** Нажатие на слайде: выбран тот объект, что под указателем; на пустом месте выделение снимается. */
  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    const obj = hit(e.clientX, e.clientY);
    onSelect(obj?.object_id ?? null);
    // Фокус — холсту при каждом нажатии, а не только при смене выделения: после правки в
    // панели свойств клик по уже выбранному объекту иначе оставлял фокус в поле ввода, и
    // Ctrl+Z, Delete и стрелки уходили туда, а preventDefault ниже фокус сам не переносит.
    ref.current?.focus({ preventScroll: true });
    if (!obj || !editable || !movable(obj)) return;
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
    if (!drag) {
      // Подсвечивается тот объект, который выделится нажатием: подсветка по :hover бралась от
      // самого верхнего прямоугольника DOM, и контейнер загорался, пока указатель шёл к буквам.
      const under = hit(e.clientX, e.clientY);
      const id = under?.object_id ?? null;
      if (id !== (hovered?.id ?? null)) setHovered(id ? { id, box: glyphBox(id) } : null);
      return;
    }
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
    // Delete (и Backspace — на маке это «стереть») убирает выделенный объект со слайда.
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      e.stopPropagation();
      onDelete?.(selectedObjectId);
      return;
    }
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

  // Группа из фигур произвольной формы — иконка, мокап, схема: рисовать её по одной фигуре
  // нельзя (у каждой только рамка, а не контур), и сотни вырезок из миниатюры браузер не
  // тянет. Такая группа показывается одной вырезкой, её дети в холст не попадают. Группы с
  // текстом остаются живыми: текст правится, и дублировать его вырезкой нельзя.
  const customGroups = new Set<string>();
  for (const group of slide.objects) {
    if (group.kind !== "group") continue;
    const children = slide.objects.filter((o) => (o.group_path ?? []).includes(group.object_id));
    if (children.length === 0) continue;
    const anyCustom = children.some((o) => o.geometry === "custom");
    const anyText = children.some(
      (o) => (o.text?.paragraphs ?? []).some((p) => (p.text ?? "").trim()) || Boolean(o.text?.plain?.trim()),
    );
    if (anyCustom && !anyText) customGroups.add(group.object_id);
  }
  const objects = [...slide.objects]
    .filter((o) => (o.kind === "group" ? customGroups.has(o.object_id) : !(o.group_path ?? []).some((g) => customGroups.has(g))))
    .sort((a, b) => a.z_order - b.z_order);
  const selected = objects.find((o) => o.object_id === selectedObjectId);
  const selectedBox = drag && selected && drag.id === selected.object_id ? drag.current : selected?.bbox;
  // Подсказка наведения обводит то, что видно: буквы у текста, рамку у остальных объектов.
  const hoverObj = hovered && hovered.id !== selectedObjectId ? objects.find((o) => o.object_id === hovered.id) : undefined;
  const hoverBox = hoverObj ? (hovered?.box ?? hoverObj.bbox) : null;

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
      onPointerLeave={() => setHovered(null)}
      onKeyDown={onKeyDown}
      onPointerDown={onPointerDown}
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
            ratio={ratio}
            thumbUrl={thumbUrl}
            mediaUrl={mediaUrl}
            selected={obj.object_id === selectedObjectId}
            hovered={obj.object_id === hovered?.id}
          />
        );
      })}
      {hoverObj && hoverBox && !drag && <div className="canvas-hover" style={percentBox(hoverBox)} data-testid="canvas-hover" />}
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
  // Группу двигают как целое только в PowerPoint: в плане правка адресуется объекту, и
  // перенос группы нечем записать. Соединители и «прочее» не двигаем по той же причине.
  return obj.kind !== "connector" && obj.kind !== "other" && obj.kind !== "group";
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
  /** Ширина слайда к высоте: радиус скругления в PowerPoint считается от меньшей стороны. */
  ratio: number;
  thumbUrl: string | undefined;
  mediaUrl: (assetId: string) => string | undefined;
  selected: boolean;
  hovered: boolean;
}

function CanvasObject({ obj, box, widthPt, ratio, thumbUrl, mediaUrl, selected, hovered }: ObjectProps) {
  const style: React.CSSProperties = { ...percentBox(box) };
  if (obj.rotation_deg) style.transform = `rotate(${obj.rotation_deg}deg)`;
  // Градиент темы рисуется основным цветом: точного градиента в ComposedDeck нет, а плашка
  // цвета темы ближе к слайду, чем прозрачная дыра на её месте.
  if ((obj.fill?.kind === "solid" || obj.fill?.kind === "gradient") && obj.fill.color) style.background = obj.fill.color;
  // Контур и скругление — как в PPTX и в HTML-экспорте: толщина линии в пунктах шаблона,
  // радиус от меньшей стороны фигуры. Раньше здесь стояли «1px» и «8% / 12%», и та же
  // плашка на холсте выглядела иначе, чем на картинке слайда.
  if (obj.line?.color) {
    const thickness = ((obj.line.width_pt ?? 0.75) / widthPt) * 100;
    // Разделитель на слайде — фигура нулевой высоты: внутренняя тень на ней не видна вовсе,
    // а вырезкой из миниатюры её не показать (делить на ноль). Рисуем линию заливкой.
    if (box.width <= 0 || box.height <= 0) {
      style.background = obj.line.color;
      if (box.height <= 0) style.height = `${thickness.toFixed(3)}cqw`;
      else style.width = `${thickness.toFixed(3)}cqw`;
    } else {
      style.boxShadow = `inset 0 0 0 ${thickness.toFixed(3)}cqw ${obj.line.color}`;
    }
  }
  if (obj.geometry === "ellipse") style.borderRadius = "50%";
  else if (obj.geometry?.startsWith("round") && box.width > 0 && box.height > 0 && ratio > 0) {
    const adjust = obj.geometry_adjust ?? DEFAULT_ROUND_ADJ;
    style.borderRadius = `${(adjust * Math.min(box.width, box.height / ratio) * 100).toFixed(3)}cqw`;
  }
  const common = {
    className: "canvas-object",
    "data-testid": `canvas-object-${obj.object_id}`,
    "data-kind": obj.kind,
    "data-selected": selected || undefined,
    "data-hover": hovered || undefined,
    "data-user": obj.user_overrides && obj.user_overrides.length > 0 ? true : undefined,
    title: obj.name ?? undefined,
  } as const;

  // Фигура произвольной формы (мокап телефона, стрелка, кольцо): её контур в ComposedDeck не
  // записан, и прямоугольник с той же заливкой давал чёрный блок там, где на слайде тонкая
  // линия. Показываем вырезку из миниатюры — как таблицы и диаграммы.
  const custom = obj.geometry === "custom" || obj.kind === "group";
  // Пустой абзац — не текст: у фигур из PowerPoint почти всегда есть txBody с пустой строкой,
  // и по «есть абзацы» иконка считалась надписью и исчезала с холста.
  const hasText = (obj.text?.paragraphs ?? []).some((p) => (p.text ?? "").trim()) || Boolean(obj.text?.plain?.trim());
  if (custom) {
    if (!hasText) return <CropObject common={common} style={{ ...percentBox(box) }} box={box} thumbUrl={thumbUrl} />;
    delete style.background;
    delete style.boxShadow;
  }

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
  // Линию рисуем сами (выше), вырезка из миниатюры для неё невозможна: нулевая сторона.
  if (CROP_KINDS.has(obj.kind)) {
    if (obj.line?.color && (box.width <= 0 || box.height <= 0)) return <div {...common} style={style} />;
    return <CropObject common={common} style={style} box={box} thumbUrl={thumbUrl} />;
  }

  const paragraphs = obj.text?.paragraphs ?? (obj.text?.plain ? [{ text: obj.text.plain }] : []);
  if (paragraphs.length === 0) return <div {...common} style={style} />;
  const insets = obj.text?.insets ?? {};
  const pad = (v: number | undefined) => `${((v ?? 0) * 100).toFixed(2)}cqw`;
  const base = obj.text?.computed_style ?? {};
  // Вертикальная привязка: в шаблонах подписи карточек стоят по центру рамки, и без этого
  // текст на холсте оказывался выше, чем на картинке слайда.
  const anchor = obj.text?.anchor;
  const anchorStyle: React.CSSProperties =
    anchor === "middle" || anchor === "bottom"
      ? { display: "flex", flexDirection: "column", justifyContent: anchor === "middle" ? "center" : "flex-end" }
      : {};
  return (
    <div
      {...common}
      style={{
        ...style,
        ...anchorStyle,
        padding: `${pad(insets.top)} ${pad(insets.right)} ${pad(insets.bottom)} ${pad(insets.left)}`,
      }}
    >
      {paragraphs.map((p, i) => {
        const font = { ...(base.font ?? {}), ...(p.style?.font ?? {}) };
        const sizePt = font.size_pt ?? 18;
        const ps: React.CSSProperties = { fontSize: `${((sizePt / widthPt) * 100).toFixed(3)}cqw` };
        if (font.family) ps.fontFamily = `"${font.family}", "Play", system-ui, sans-serif`;
        if (font.color) ps.color = font.color;
        if (font.bold) ps.fontWeight = 700;
        if (font.italic) ps.fontStyle = "italic";
        // 100% межстрочного — одинарный интервал шрифта: его браузер и так ставит сам.
        if (font.line_spacing && Math.abs(font.line_spacing - 1) > 0.01) {
          ps.lineHeight = (font.line_spacing * naturalLine(font.family ?? "", Boolean(font.bold), Boolean(font.italic))).toFixed(3);
        }
        if (p.align && p.align !== "left") ps.textAlign = p.align;
        const spacing = p.style ?? base;
        // Отступ перед абзацем — только между абзацами: рендерер не добавляет его первому
        // абзацу рамки, и с ним весь текст на холсте стоял на строку ниже, чем на слайде.
        if (i > 0 && spacing.space_before_pt) ps.marginTop = `${((spacing.space_before_pt / widthPt) * 100).toFixed(3)}cqw`;
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
