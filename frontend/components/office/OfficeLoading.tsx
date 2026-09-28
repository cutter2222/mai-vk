"use client";

import { Group, Loader, Stack, Text } from "@mantine/core";
import { IconCheck, IconClock, IconPresentation } from "@tabler/icons-react";
import { useState } from "react";

import { formatMs } from "@/lib/format";
import { useElapsed } from "@/lib/hooks/useElapsed";

import styles from "./OfficeLoading.module.css";

/** Этапы открытия редактора: копия на сервере → программа ONLYOFFICE (`onAppReady`) →
 *  слайды (`onDocumentReady`). */
export type OfficeStage = "copy" | "app" | "slides";

export const OFFICE_STAGES: { id: OfficeStage; label: string }[] = [
  { id: "copy", label: "Готовим копию на сервере" },
  { id: "app", label: "Запускаем редактор" },
  { id: "slides", label: "Открываем слайды" },
];

/** Заставка вместо стандартного загрузчика ONLYOFFICE: в стиле индикатора генерации,
 *  с пройденными этапами и временем от начала открытия. Текст «Загружается редактор…»
 *  остаётся: по нему живые тесты ждут готовности. */
export function OfficeLoading({ stage }: { stage: OfficeStage }) {
  const [startedAt] = useState(() => new Date().toISOString());
  const elapsed = useElapsed(startedAt);
  const current = OFFICE_STAGES.findIndex((s) => s.id === stage);
  return <div className={`office-loading ${styles.root}`} data-testid="office-loading" data-stage={stage} aria-busy="true">
    <Stack gap="md" className={styles.content}>
      <div className={styles.magic} aria-hidden="true"><IconPresentation size={40} stroke={1.4} /></div>
      <Group justify="space-between" wrap="nowrap">
        <Text fw={600} size="lg">Загружается редактор…</Text>
        {elapsed != null && <Group gap={6} wrap="nowrap" className={styles.timer}>
          <IconClock size={18} stroke={1.8} aria-hidden />
          <Text fw={600} style={{ whiteSpace: "nowrap" }}>{formatMs(elapsed)}</Text>
        </Group>}
      </Group>
      <Stack gap={6} role="status" aria-live="polite">
        {OFFICE_STAGES.map((s, i) => {
          const state = i < current ? "done" : i === current ? "active" : "todo";
          return <Group key={s.id} gap="xs" wrap="nowrap" className={styles[state]} data-testid={`office-stage-${s.id}`} data-state={state}>
            {state === "done" ? <IconCheck size={16} stroke={2.2} aria-hidden /> : state === "active" ? <Loader size={14} /> : <span className={styles.dot} aria-hidden />}
            <Text size="sm">{s.label}</Text>
          </Group>;
        })}
      </Stack>
      <div className={styles.track} role="progressbar" aria-label="Открываем редактор"><div className={styles.fill} style={{ width: `${Math.round(((current + 0.5) / OFFICE_STAGES.length) * 100)}%` }} /></div>
    </Stack>
  </div>;
}
