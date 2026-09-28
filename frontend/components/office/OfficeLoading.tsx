"use client";

import { Loader, Stack, Text } from "@mantine/core";
import { IconPresentation } from "@tabler/icons-react";

import styles from "./OfficeLoading.module.css";

/** Этапы открытия редактора: копия на сервере → программа ONLYOFFICE (`onAppReady`) →
 *  слайды (`onDocumentReady`). */
export type OfficeStage = "copy" | "app" | "slides";

const PHRASES: Record<OfficeStage, string> = {
  copy: "Готовим копию на сервере…",
  app: "Запускаем редактор…",
  slides: "Открываем слайды…",
};

/** Заставка вместо стандартного загрузчика ONLYOFFICE: значок и одна фраза по этапу.
 *  Живые тесты ждут исчезновения `office-loading`. */
export function OfficeLoading({ stage }: { stage: OfficeStage }) {
  return <div className={`office-loading ${styles.root}`} data-testid="office-loading" data-stage={stage} aria-busy="true">
    <Stack gap="md" align="center">
      <div className={styles.magic} aria-hidden="true"><IconPresentation size={40} stroke={1.4} /></div>
      <Loader size="sm" />
      <Text key={stage} fw={600} size="lg" className={styles.phrase} role="status" aria-live="polite">{PHRASES[stage]}</Text>
    </Stack>
  </div>;
}
