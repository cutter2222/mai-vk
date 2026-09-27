"use client";

import { useSyncExternalStore, type DragEvent } from "react";

import type { OfficeImageSource } from "@/lib/api/client";

/**
 * Что сейчас тащат из панели «Файлы» в редактор: картинку на слайд или готовый слайд шаблона.
 * Значение живёт вне React, как состояние панели в `panel.ts`: карточка в левой панели и
 * слой-приёмник над редактором — разные ветки дерева. Слой нужен потому, что iframe ONLYOFFICE
 * забирает события перетаскивания себе, и без прозрачной крышки родитель броска не увидит.
 */
export type DraggedImage = OfficeImageSource & { name: string };

/** Слайд шаблона (номер с единицы): встаёт в презентацию следом за текущим слайдом. */
export type DraggedSlide = { template_id: string; slide: number; name: string };

export type DraggedItem = DraggedImage | DraggedSlide;

export const isDraggedSlide = (item: DraggedItem): item is DraggedSlide => "slide" in item;

/** Тип данных перетаскивания: Firefox не начинает перетаскивание без данных. */
export const DRAG_TYPE = "application/x-pd-image";

let dragged: DraggedItem | null = null;
let pending: ReturnType<typeof setTimeout> | undefined;
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function setDragged(value: DraggedItem | null): void {
  clearTimeout(pending);
  dragged = value;
  listeners.forEach((listener) => listener());
}

export function draggedItem(): DraggedItem | null {
  return dragged;
}

export function useDragged(): DraggedItem | null {
  return useSyncExternalStore(subscribe, draggedItem, () => null);
}

/** Свойства карточки, которую можно бросить в редактор. */
export function dragProps(item: DraggedItem) {
  return {
    draggable: true,
    onDragStart: (event: DragEvent<HTMLElement>) => {
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData(DRAG_TYPE, JSON.stringify(item));
      // Слой-приёмник появляется со следующего такта: правку DOM прямо в dragstart Chrome
      // иногда принимает за отмену перетаскивания.
      clearTimeout(pending);
      pending = setTimeout(() => setDragged(item), 0);
    },
    onDragEnd: () => setDragged(null),
  };
}
