"use client";

import { Box, Text } from "@mantine/core";
import { useEffect, useRef, useState } from "react";

import type { Bbox } from "@/lib/api/types";

export interface Overlay {
  id: string;
  bbox: Bbox;
  severity: string;
  label: string;
}

interface Props {
  src?: string;
  alt: string;
  overlays?: Overlay[];
  activeOverlay?: string | null;
  onOverlayClick?: (id: string) => void;
  onLoad?: () => void;
}

/**
 * Слайд 16:9 с рамками находок. Координаты рамок нормализованы к слайду, поэтому
 * пересчитываются в пиксели фактической области изображения внутри контейнера (object-fit: contain).
 */
export function SlideImage({ src, alt, overlays = [], activeOverlay, onOverlayClick }: Props) {
  const ref = useRef<HTMLImageElement | null>(null);
  const [rect, setRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null);

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

  return (
    <Box className="slide-frame" data-testid="slide-frame">
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img ref={ref} src={src} alt={alt} />
      ) : (
        <Text c="dimmed" size="sm" style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}>
          Миниатюра ещё не готова
        </Text>
      )}
      {rect &&
        overlays.map((o) => (
          <Box
            key={o.id}
            className="issue-box"
            data-severity={o.severity}
            data-active={activeOverlay === o.id}
            data-testid={`issue-box-${o.id}`}
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
