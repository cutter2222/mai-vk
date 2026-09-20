"use client";

import { useSyncExternalStore } from "react";

/**
 * Свёрнута ли левая панель редактора. Значение живёт вне React и в localStorage: сворачивать
 * её при каждом заходе утомительно, а состояние нужно и панели, и рельсу рядом с ней.
 *
 * Чтение через `useSyncExternalStore`, а не эффектом: при статическом экспорте первый кадр
 * рисуется без браузера, и серверный снимок («развёрнута») отличается от сохранённого.
 */

const KEY = "pd.panel";

let open: boolean | null = null;
const listeners = new Set<() => void>();

function read(): boolean {
  if (open === null) {
    try {
      open = window.localStorage.getItem(KEY) !== "closed";
    } catch {
      open = true; // приватный режим браузера: панель просто останется развёрнутой
    }
  }
  return open;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function setPanelOpen(value: boolean): void {
  open = value;
  try {
    window.localStorage.setItem(KEY, value ? "open" : "closed");
  } catch {
    // запись недоступна — состояние живёт до перезагрузки
  }
  listeners.forEach((listener) => listener());
}

export function usePanelOpen(): boolean {
  return useSyncExternalStore(subscribe, read, () => true);
}
