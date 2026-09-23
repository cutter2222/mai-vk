"use client";

import { useSyncExternalStore, type DragEvent } from "react";

import type { OfficeImageSource } from "@/lib/api/client";

/**
 * Какую картинку сейчас тащат из панели «Файлы» на слайд. Значение живёт вне React, как
 * состояние панели в `panel.ts`: карточка в левой панели и слой-приёмник над редактором —
 * разные ветки дерева. Слой нужен потому, что iframe ONLYOFFICE забирает события
 * перетаскивания себе, и без прозрачной крышки родитель броска не увидит.
 */
export type DraggedImage = OfficeImageSource & { name: string };

/** Тип данных перетаскивания: Firefox не начинает перетаскивание без данных. */
export const DRAG_TYPE = "application/x-pd-image";

let dragged: DraggedImage | null = null;
let pending: ReturnType<typeof setTimeout> | undefined;
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function setDraggedImage(value: DraggedImage | null): void {
  clearTimeout(pending);
  dragged = value;
  listeners.forEach((listener) => listener());
}

export function draggedImage(): DraggedImage | null {
  return dragged;
}

export function useDraggedImage(): DraggedImage | null {
  return useSyncExternalStore(subscribe, draggedImage, () => null);
}

/** Свойства карточки, которую можно бросить на слайд. */
export function imageDragProps(item: DraggedImage) {
  return {
    draggable: true,
    onDragStart: (event: DragEvent<HTMLElement>) => {
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData(DRAG_TYPE, JSON.stringify(item));
      // Слой-приёмник появляется со следующего такта: правку DOM прямо в dragstart Chrome
      // иногда принимает за отмену перетаскивания.
      clearTimeout(pending);
      pending = setTimeout(() => setDraggedImage(item), 0);
    },
    onDragEnd: () => setDraggedImage(null),
  };
}
