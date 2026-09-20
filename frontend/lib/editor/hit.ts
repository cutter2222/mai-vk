import type { Bbox } from "@/lib/api/types";

import type { DeckObject } from "./overrides";

/** Насколько близко к краю надо попасть, чтобы выделить пустую рамку. */
export const EDGE_PX = 8;

export interface HitCandidate {
  bbox: Bbox;
  z: number;
  /** Пустая рамка: середина кликов не ловит, клик проходит к тому, что под ней. */
  hollow: boolean;
}

/**
 * Что лежит под точкой (доли слайда) — тем же правилом на картинке слайда и на холсте
 * редактора. Пустые рамки-контейнеры ловят только края: иначе клик по буквам доставался
 * прозрачному прямоугольнику поверх них. Если под точкой одни такие рамки — ничего не
 * выбрано: нажатие на пустое место снимает выделение, а не цепляет контейнер.
 */
export function hitTest<T extends HitCandidate>(items: T[], fx: number, fy: number, ex: number, ey: number): T | null {
  const under = items
    .filter((o) => fx >= o.bbox.x && fx <= o.bbox.x + o.bbox.width && fy >= o.bbox.y && fy <= o.bbox.y + o.bbox.height)
    .sort((a, b) => b.z - a.z);
  for (const o of under) {
    if (!o.hollow) return o;
    const b = o.bbox;
    if (fx - b.x < ex || b.x + b.width - fx < ex || fy - b.y < ey || b.y + b.height - fy < ey) return o;
  }
  return null;
}

/** Пустая рамка: ни заливки, ни текста, ни картинки. */
export function isHollow(obj: DeckObject): boolean {
  const filled = obj.fill?.kind === "solid" || obj.fill?.kind === "gradient" || obj.fill?.kind === "image";
  const text = (obj.text?.plain ?? "").trim().length > 0;
  return !filled && !text && obj.kind !== "picture";
}
