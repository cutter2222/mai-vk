"use client";

import { Stack, Text } from "@mantine/core";
import { IconPresentation } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";

import styles from "./OfficeLoading.module.css";

/** Этапы открытия редактора: копия на сервере → программа ONLYOFFICE (`onAppReady`) →
 *  слайды (`onDocumentReady`). */
export type OfficeStage = "copy" | "app" | "slides";

const PHRASES: Record<OfficeStage, string> = {
  copy: "Готовим копию на сервере",
  app: "Запускаем редактор",
  slides: "Открываем слайды",
};

/** Меньше этого фраза на экране не живёт: этапы сменяются за доли секунды, и текст прыгал. */
export const MIN_PHRASE_MS = 3500;

/** Заставка вместо стандартного загрузчика ONLYOFFICE: значок и одна фраза по этапу с
 *  бегущим по кругу многоточием вместо кружка. Фраза сменяется размеренно: следующий этап
 *  ждёт, пока текущая пробудет на экране хотя бы `MIN_PHRASE_MS`; готовый редактор заставку
 *  снимает сразу. Живые тесты ждут исчезновения `office-loading`. */
/** Фразы по этапам можно заменить: просмотр шаблона копии не делает, у него свои слова. */
export function OfficeLoading({ stage, phrases }: { stage: OfficeStage; phrases?: Partial<Record<OfficeStage, string>> }) {
  const [shown, setShown] = useState<OfficeStage>(stage);
  // Момент, когда текущая фраза появилась; заполняется в эффекте, а не при рендере.
  const since = useRef<number | null>(null);
  useEffect(() => {
    since.current ??= Date.now();
    if (stage === shown) return;
    const wait = Math.max(0, MIN_PHRASE_MS - (Date.now() - since.current));
    const timer = setTimeout(() => { since.current = Date.now(); setShown(stage); }, wait);
    return () => clearTimeout(timer);
  }, [stage, shown]);
  return <div className={`office-loading ${styles.root}`} data-testid="office-loading" data-stage={shown} aria-busy="true">
    <Stack gap="md" align="center">
      <div className={styles.magic} aria-hidden="true"><IconPresentation size={40} stroke={1.4} /></div>
      <Text key={shown} fw={600} size="lg" className={styles.phrase} role="status" aria-live="polite">
        {phrases?.[shown] ?? PHRASES[shown]}<span className={styles.dots} aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>
      </Text>
    </Stack>
  </div>;
}
