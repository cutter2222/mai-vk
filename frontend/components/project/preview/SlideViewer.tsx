"use client";

import { ActionIcon, Text } from "@mantine/core";
import { IconChevronDown, IconChevronUp, IconGripVertical } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";

import { SlideImage, type Outline, type Overlay } from "@/components/common/SlideImage";

export interface ViewerSlide {
  key: string;
  /** Номер слайда в колоде. Место в ленте может отличаться: черновик редактора переставляет слайды. */
  deckIndex: number;
  src?: string;
  label: string;
  /** Отметка на миниатюре: на слайде есть находки аудита. */
  flagged?: boolean;
  /** Слайд переставлен или изменён в черновике редактора. */
  edited?: boolean;
}

interface Props {
  slides: ViewerSlide[];
  index: number;
  onIndex: (i: number) => void;
  overlays?: Overlay[];
  activeOverlay?: string | null;
  onOverlayClick?: (id: string) => void;
  outlines?: Outline[];
  onOutlineClick?: (id: string) => void;
  /** Подпись слева от счётчика: «ревизия 2». */
  caption?: React.ReactNode;
  /** Элементы справа от счётчика. */
  actions?: React.ReactNode;
  /** Содержимое под слайдом в основной колонке: обоснование варианта, «как собран», панель свойств. */
  children?: React.ReactNode;
  /** Боковая панель справа от слайда: находки аудита. */
  aside?: React.ReactNode;
  /** Замена картинки слайда: живой холст редактора. */
  stage?: React.ReactNode;
  /** Перестановка миниатюр перетаскиванием (за иконку захвата или удержанием) и Alt+стрелками. */
  onReorder?: (from: number, to: number) => void;
  editing?: boolean;
  /** Пропорция слайда колоды (ширина к высоте). По умолчанию 16:9. */
  ratio?: number;
}

const HOLD_MS = 250;
const HOLD_SLOP = 6;
/** Длительность выезда правой колонки; совпадает с --side-ms в globals.css. */
const SIDE_MS = 240;

/**
 * Вертикальная лента миниатюр слева и крупный слайд справа, как в редакторах презентаций.
 * Слайды вариантов генерации; образцы шаблона показываются в его карточке в библиотеке.
 */
export function SlideViewer({ slides, index, onIndex, overlays, activeOverlay, onOverlayClick, outlines, onOutlineClick, caption, actions, children, aside, stage, onReorder, editing, ratio }: Props) {
  const total = slides.length;
  const safeIndex = Math.min(index, Math.max(total - 1, 0));
  const current = slides[safeIndex];
  const stripRef = useRef<HTMLDivElement | null>(null);
  const [drag, setDrag] = useState<{ from: number; to: number; y: number } | null>(null);
  const hold = useRef<{ timer: number; from: number; x: number; y: number; pointerId: number } | null>(null);
  // Правая колонка выезжает, а не появляется скачком: пока идёт обратный ход, в ней держится
  // прежнее содержимое, иначе панель схлопывалась бы пустой.
  const asideOpen = Boolean(aside);
  const lastAside = useRef<React.ReactNode>(null);
  if (aside) lastAside.current = aside;
  const [keepAside, setKeepAside] = useState(asideOpen);
  useEffect(() => {
    if (asideOpen) {
      setKeepAside(true);
      return;
    }
    const timer = window.setTimeout(() => {
      lastAside.current = null;
      setKeepAside(false);
    }, SIDE_MS);
    return () => window.clearTimeout(timer);
  }, [asideOpen]);
  const asideNode = aside ?? (keepAside ? lastAside.current : null);

  // Активная миниатюра держится в видимой части ленты при листании клавишами.
  useEffect(() => {
    const active = stripRef.current?.querySelector<HTMLElement>('[data-active="true"]');
    active?.scrollIntoView({ block: "nearest" });
  }, [safeIndex]);

  // Место вставки: сколько миниатюр (кроме перетаскиваемой) лежат серединой выше указателя.
  const targetIndex = (clientY: number, from: number): number => {
    const buttons = Array.from(stripRef.current?.querySelectorAll<HTMLElement>("[data-thumb]") ?? []);
    let count = 0;
    buttons.forEach((b, i) => {
      if (i === from) return;
      const r = b.getBoundingClientRect();
      if (clientY > r.top + r.height / 2) count += 1;
    });
    return Math.min(count, Math.max(buttons.length - 1, 0));
  };

  const beginDrag = (from: number, y: number, pointerId: number) => {
    if (!onReorder) return;
    try {
      stripRef.current?.setPointerCapture(pointerId);
    } catch {
      // захват недоступен (например, в тестах) — перетаскивание работает внутри ленты
    }
    setDrag({ from, to: from, y });
  };

  const onStripPointerMove = (e: React.PointerEvent) => {
    const h = hold.current;
    if (h && !drag) {
      if (Math.abs(e.clientX - h.x) > HOLD_SLOP || Math.abs(e.clientY - h.y) > HOLD_SLOP) {
        window.clearTimeout(h.timer);
        hold.current = null;
      }
      return;
    }
    if (drag) {
      e.preventDefault();
      setDrag({ ...drag, to: targetIndex(e.clientY, drag.from), y: e.clientY });
    }
  };

  const endDrag = () => {
    if (hold.current) {
      window.clearTimeout(hold.current.timer);
      hold.current = null;
    }
    if (drag) {
      if (drag.from !== drag.to) onReorder?.(drag.from, drag.to);
      setDrag(null);
    }
  };

  const onThumbPointerDown = (e: React.PointerEvent, i: number, grip: boolean) => {
    if (!onReorder || e.button !== 0) return;
    if (grip) {
      e.preventDefault();
      beginDrag(i, e.clientY, e.pointerId);
      return;
    }
    const pointerId = e.pointerId;
    const y = e.clientY;
    const timer = window.setTimeout(() => {
      hold.current = null;
      beginDrag(i, y, pointerId);
    }, HOLD_MS);
    hold.current = { timer, from: i, x: e.clientX, y: e.clientY, pointerId: e.pointerId };
  };

  const onThumbKeyDown = (e: React.KeyboardEvent, i: number) => {
    if (!onReorder || !e.altKey) return;
    if (e.key === "ArrowUp" && i > 0) {
      e.preventDefault();
      e.stopPropagation();
      onReorder(i, i - 1);
    }
    if (e.key === "ArrowDown" && i < total - 1) {
      e.preventDefault();
      e.stopPropagation();
      onReorder(i, i + 1);
    }
  };

  return (
    <div
      className="viewer"
      data-aside={asideOpen}
      data-editing={Boolean(editing)}
      style={ratio ? ({ "--stage-ratio": ratio, "--slide-ratio": ratio } as React.CSSProperties) : undefined}
    >
      {total > 0 && (
        <div
          className="filmstrip"
          ref={stripRef}
          data-testid="thumb-strip"
          data-dragging={drag ? "true" : undefined}
          onPointerMove={onStripPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onPointerLeave={() => {
            if (hold.current) {
              window.clearTimeout(hold.current.timer);
              hold.current = null;
            }
          }}
        >
          {slides.map((s, i) => (
            <button
              key={s.key}
              type="button"
              data-thumb
              data-active={i === safeIndex}
              data-drag-source={drag && drag.from === i ? "true" : undefined}
              data-drop-target={drag && drag.to === i && drag.to !== drag.from ? (drag.to < drag.from ? "before" : "after") : undefined}
              onClick={() => onIndex(i)}
              onPointerDown={(e) => onThumbPointerDown(e, i, false)}
              onKeyDown={(e) => onThumbKeyDown(e, i)}
              aria-label={s.label}
              title={onReorder ? `${s.label} · перетащите за иконку, Alt+↑↓ — переставить` : s.label}
              data-testid={`thumb-${i}`}
            >
              <span className="thumb-number">{i + 1}</span>
              <span className="thumb-image">
                <SlideImage src={s.src} alt={s.label} />
                {s.flagged && <span className="thumb-issues" />}
                {s.edited && <span className="thumb-edited" title="изменён в черновике" />}
                {onReorder && (
                  <span
                    className="thumb-grip"
                    role="presentation"
                    data-testid={`thumb-grip-${i}`}
                    onPointerDown={(e) => {
                      e.stopPropagation();
                      onThumbPointerDown(e, i, true);
                    }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <IconGripVertical size={14} />
                  </span>
                )}
              </span>
            </button>
          ))}
        </div>
      )}
      <div className="viewer-main" data-editing={Boolean(editing)}>
        {/* Слайд и подпись под ним — один блок по центру свободной высоты: подпись держится
            у нижнего края слайда, а не у нижнего края экрана, как было раньше. */}
        <div className="viewer-stage">
          <div className="stage-column">
            <div className="preview-stage">
              {stage ?? (
                <SlideImage
                  src={current?.src}
                  alt={current?.label ?? "Слайд"}
                  overlays={overlays}
                  activeOverlay={activeOverlay}
                  onOverlayClick={onOverlayClick}
                  outlines={outlines}
                  onOutlineClick={onOutlineClick}
                />
              )}
            </div>
            <div className="viewer-bar">
              <div className="viewer-bar-meta">{caption}</div>
              <div className="viewer-bar-pager">
                <ActionIcon variant="subtle" color="gray" size="md" disabled={safeIndex <= 0} onClick={() => onIndex(safeIndex - 1)} aria-label="Предыдущий слайд">
                  <IconChevronUp size={16} />
                </ActionIcon>
                <Text size="sm" c="dimmed" style={{ minWidth: 104, textAlign: "center" }} data-testid="slide-counter">
                  {total ? `Слайд ${safeIndex + 1} из ${total}` : "Слайдов нет"}
                </Text>
                <ActionIcon variant="subtle" color="gray" size="md" disabled={safeIndex >= total - 1} onClick={() => onIndex(safeIndex + 1)} aria-label="Следующий слайд">
                  <IconChevronDown size={16} />
                </ActionIcon>
              </div>
              <div className="viewer-bar-actions">{actions}</div>
            </div>
          </div>
        </div>
        {children ? <div className="viewer-extra">{children}</div> : null}
      </div>
      {/* Колонка в разметке всегда: ширина идёт от нуля, поэтому выезд плавный и в обе стороны. */}
      <div className="viewer-side" data-open={asideOpen} aria-hidden={!asideOpen} inert={!asideOpen}>
        <div className="viewer-side-inner">{asideNode}</div>
      </div>
    </div>
  );
}
