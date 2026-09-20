"use client";

import { Box, Text } from "@mantine/core";
import { useEffect, useRef, useState } from "react";

import type { Bbox } from "@/lib/api/types";
import { EDGE_PX, hitTest } from "@/lib/editor/hit";

export interface Overlay {
  id: string;
  bbox: Bbox;
  severity: string;
  label: string;
}

export interface Outline {
  id: string;
  bbox: Bbox;
  label: string;
  /** Порядок в стопке слайда: под указателем выбирается верхний. */
  z: number;
  /** Пустая рамка-контейнер: ловит только края, клик по буквам достаётся тексту под ней. */
  hollow: boolean;
}

interface Props {
  src?: string;
  alt: string;
  overlays?: Overlay[];
  activeOverlay?: string | null;
  onOverlayClick?: (id: string) => void;
  onLoad?: () => void;
  /** Контуры объектов слайда (из ComposedDeck): подсвечиваются при наведении, клик открывает редактор. */
  outlines?: Outline[];
  onOutlineClick?: (id: string) => void;
}

/**
 * Слайд 16:9 с рамками находок. Координаты рамок нормализованы к слайду, поэтому
 * пересчитываются в пиксели фактической области изображения внутри контейнера (object-fit: contain).
 */
export function SlideImage({ src, alt, overlays = [], activeOverlay, onOverlayClick, outlines = [], onOutlineClick }: Props) {
  const ref = useRef<HTMLImageElement | null>(null);
  const frameRef = useRef<HTMLDivElement | null>(null);
  const [rect, setRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null);
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => {
    const img = ref.current;
    if (!img) return;
    const update = () => {
      const box = img.getBoundingClientRect();
      const natural = img.naturalWidth / (img.naturalHeight || 1) || 16 / 9;
      const boxRatio = box.width / (box.height || 1);
      let width = box.width;
      let height = box.height;
      if (boxRatio > natural) width = box.height * natural;
      else height = box.width / natural;
      setRect({ left: (box.width - width) / 2, top: (box.height - height) / 2, width, height });
    };
    update();
    img.addEventListener("load", update);
    const ro = new ResizeObserver(update);
    ro.observe(img);
    return () => {
      img.removeEventListener("load", update);
      ro.disconnect();
    };
  }, [src]);

  // Что под указателем: тем же правилом, что и на холсте редактора. Пустая середина
  // контейнера пропускает указатель дальше, а на голом фоне не выделяется ничего.
  const pick = (clientX: number, clientY: number): Outline | null => {
    const frame = frameRef.current;
    if (!frame || !rect || !rect.width || !rect.height) return null;
    const box = frame.getBoundingClientRect();
    return hitTest(
      outlines,
      (clientX - box.left - rect.left) / rect.width,
      (clientY - box.top - rect.top) / rect.height,
      EDGE_PX / rect.width,
      EDGE_PX / rect.height,
    );
  };

  const interactive = outlines.length > 0 && Boolean(onOutlineClick);

  return (
    <Box
      className="slide-frame"
      data-testid="slide-frame"
      ref={frameRef}
      data-pick={interactive && hover ? true : undefined}
      onPointerMove={interactive ? (e) => {
        const id = pick(e.clientX, e.clientY)?.id ?? null;
        if (id !== hover) setHover(id);
      } : undefined}
      onPointerLeave={interactive ? () => setHover(null) : undefined}
      onClick={interactive ? (e) => {
        // Рамки находок аудита лежат тут же и живут своим кликом.
        if ((e.target as HTMLElement).closest(".issue-box")) return;
        const found = pick(e.clientX, e.clientY);
        if (found) onOutlineClick?.(found.id);
      } : undefined}
    >
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img ref={ref} src={src} alt={alt} />
      ) : (
        <Text c="dimmed" size="sm" style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}>
          Миниатюра ещё не готова
        </Text>
      )}
      {rect &&
        outlines.map((o) => (
          <button
            key={`outline-${o.id}`}
            type="button"
            className="object-outline"
            data-testid={`object-outline-${o.id}`}
            data-hover={hover === o.id || undefined}
            title={o.label}
            aria-label={o.label}
            onClick={() => onOutlineClick?.(o.id)}
            style={{
              left: rect.left + o.bbox.x * rect.width,
              top: rect.top + o.bbox.y * rect.height,
              width: o.bbox.width * rect.width,
              height: o.bbox.height * rect.height,
            }}
          />
        ))}
      {rect &&
        overlays.map((o) => (
          <Box
            key={o.id}
            className="issue-box"
            data-severity={o.severity}
            data-active={activeOverlay === o.id}
            data-testid={`issue-box-${o.id}`}
            data-label={o.label}
            title={o.label}
            onClick={() => onOverlayClick?.(o.id)}
            style={{
              left: rect.left + o.bbox.x * rect.width,
              top: rect.top + o.bbox.y * rect.height,
              width: o.bbox.width * rect.width,
              height: o.bbox.height * rect.height,
            }}
          />
        ))}
    </Box>
  );
}
