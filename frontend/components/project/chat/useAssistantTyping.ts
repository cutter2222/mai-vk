"use client";

import { useReducedMotion } from "@mantine/hooks";
import { useEffect, useState } from "react";

import type { ChatMessage } from "@/lib/state/projects";

const PAUSE = 450;
const TICK = 35;

/** Только представление: в хранилище и на сервере всегда остаётся полный текст. */
export function useAssistantTyping(projectId: string, messages: ChatMessage[], initiallyDisplayed = messages.length) {
  const reducedMotion = useReducedMotion();
  const [state, setState] = useState(() => ({ projectId, through: initiallyDisplayed, elapsed: 0 }));
  if (state.projectId !== projectId) {
    setState({ projectId, through: initiallyDisplayed, elapsed: 0 });
  } else if (reducedMotion && state.through !== messages.length) {
    setState({ projectId, through: messages.length, elapsed: 0 });
  }

  // Лента append-only. Индекс сохраняется при замене optimistic tmp_id серверным id,
  // поэтому подтверждение сервера не начинает печать заново. История при входе уже прочитана.
  const activeIndex = messages.findIndex((m, i) => i >= state.through && m.role === "assistant" && m.kind === "text");
  const active = messages[activeIndex];
  const text = active?.kind === "text" ? active.text : "";
  const characters = Array.from(text);
  const duration = Math.min(1800, Math.max(280, characters.length * 16));
  const waiting = state.elapsed < PAUSE;
  const visibleCount = Math.floor(characters.length * Math.max(0, state.elapsed - PAUSE) / duration);

  useEffect(() => {
    if (reducedMotion || activeIndex < 0) return;
    const timer = window.setInterval(() => {
      setState((s) => s.elapsed + TICK >= PAUSE + duration
        ? { ...s, through: activeIndex + 1, elapsed: 0 }
        : { ...s, elapsed: s.elapsed + TICK });
    }, TICK);
    return () => window.clearInterval(timer);
  }, [activeIndex, duration, reducedMotion]);

  return {
    activeIndex: reducedMotion ? -1 : activeIndex,
    waiting,
    progress: state.elapsed,
    present(message: ChatMessage, index: number): ChatMessage | null {
      if (reducedMotion || state.projectId !== projectId || message.role !== "assistant" || message.kind !== "text" || index < state.through) return message;
      if (index !== activeIndex || waiting) return null;
      return { ...message, text: characters.slice(0, visibleCount).join("") };
    },
  };
}