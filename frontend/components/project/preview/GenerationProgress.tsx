"use client";

import { Group, Loader, Progress, Stack, Text } from "@mantine/core";
import { IconClock, IconSparkles } from "@tabler/icons-react";
import { useEffect, useState } from "react";

import { formatMs, STAGE_LABELS } from "@/lib/format";
import { useElapsed } from "@/lib/hooks/useElapsed";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";

import styles from "./GenerationProgress.module.css";

const PHRASES = ["Делаем магию ✨", "Превращаем идеи в слайды", "Немного терпения — ИИ работает", "Ваша история обретает форму"];

/** Только серверный процент; при неизвестном прогрессе — неопределённая анимация. */
export function GenerationProgress({ session, compact = false, starting = false }: {
  session: GenerationSession; compact?: boolean; starting?: boolean;
}) {
  const [phrase, setPhrase] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setPhrase((value) => (value + 1) % PHRASES.length), 4200);
    return () => clearInterval(timer);
  }, []);
  const result = starting ? null : session.result;
  // Таймер виден сразу: при запуске отсчёт идёт с появления индикатора, затем — от создания
  // задания на сервере.
  const [shownAt] = useState(() => new Date().toISOString());
  const elapsed = useElapsed(result?.created_at ?? (starting ? shownAt : null));
  const rawPercent = result?.progress?.percent;
  const percent = typeof rawPercent === "number" && Number.isFinite(rawPercent)
    ? Math.max(0, Math.min(100, rawPercent)) : null;
  const message = starting ? "Подготавливаем материалы и запускаем генерацию…"
    : result?.progress?.message || (result ? STAGE_LABELS[result.stage] : "Подключаемся к заданию…");

  return <div className={compact ? styles.compact : "preview-empty"} data-testid="generation-progress" aria-busy="true">
    <Stack gap={compact ? "xs" : "lg"} className={styles.content}>
      {!compact && <div className={styles.magic} aria-hidden="true"><IconSparkles size={44} stroke={1.4} /></div>}
      <Group justify="space-between" wrap="nowrap">
        <Text key={phrase} fw={600} size={compact ? "sm" : "xl"} className={styles.phrase} data-testid="generation-phrase">{PHRASES[phrase]}</Text>
        {elapsed != null && <Group gap={6} wrap="nowrap" className={styles.timer} data-testid="generation-timer">
          <IconClock size={compact ? 18 : 22} stroke={1.8} aria-hidden />
          <Text fw={600} size={compact ? "md" : "xl"} style={{ whiteSpace: "nowrap" }}>{formatMs(elapsed)}</Text>
        </Group>}
      </Group>
      <div role="status" aria-live="polite"><Group gap="xs" wrap="nowrap"><Loader size={16} /><Text size="sm">{message}</Text></Group></div>
      {!starting && session.job.error && <Text size="sm" c="orange" role="alert">Не удалось обновить статус: {session.job.error.message}. Пробуем подключиться снова.</Text>}
      {percent != null ? <Stack gap={4}>
        <Progress value={percent} animated aria-label="Прогресс генерации" aria-valuenow={percent} />
        <Text size="xs" c="dimmed" ta="right">{Math.round(percent)}%</Text>
      </Stack> : <div className={styles.track} role="progressbar" aria-label="Генерация выполняется"><div className={styles.shimmer} /></div>}
      {!compact && <Text size="sm" c="dimmed" ta="center">Готовые слайды появятся здесь автоматически. Можно оставаться в чате — генерация продолжится.</Text>}
    </Stack>
  </div>;
}