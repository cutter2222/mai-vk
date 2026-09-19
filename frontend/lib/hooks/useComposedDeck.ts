"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api/client";
import type { ComposedDeck } from "@/lib/api/types";

const cache = new Map<string, ComposedDeck>();

/**
 * Описание собранной колоды (ComposedDeck) просматриваемой ревизии: имя артефакта берётся
 * из варианта с подстановкой номера ревизии, как у миниатюр. Ревизии неизменяемы, поэтому
 * документ кэшируется по имени артефакта.
 */
export function useComposedDeck(jobId: string | null, artifact: string | undefined, currentRevision: number, viewRevision: number) {
  const name = artifact && viewRevision !== currentRevision ? artifact.replace(`/r${currentRevision}/`, `/r${viewRevision}/`) : artifact;
  const key = jobId && name ? `${jobId}:${name}` : null;
  const [loaded, setLoaded] = useState<{ key: string | null; deck: ComposedDeck | null; error: string | null }>({ key: null, deck: null, error: null });
  const cached = key ? cache.get(key) : undefined;

  useEffect(() => {
    if (!key || !jobId || !name || cache.has(key)) return;
    let alive = true;
    api.generations
      .artifactJson<ComposedDeck>(jobId, name)
      .then((deck) => {
        if (!deck || !Array.isArray(deck.slides)) throw new Error("описание колоды не получено");
        cache.set(key, deck);
        if (alive) setLoaded({ key, deck, error: null });
      })
      .catch((e: unknown) => alive && setLoaded({ key, deck: null, error: e instanceof Error ? e.message : "описание колоды не получено" }));
    return () => {
      alive = false;
    };
  }, [key, jobId, name]);

  if (cached) return { deck: cached, error: null, loading: false };
  const fresh = loaded.key === key;
  return { deck: fresh ? loaded.deck : null, error: fresh ? loaded.error : null, loading: Boolean(key) && !fresh };
}
