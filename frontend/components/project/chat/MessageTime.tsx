import { formatDate } from "@/lib/format";
import styles from "./MessageTime.module.css";

/** Дата события, а не момент отрисовки или завершения анимации. */
export function MessageTime({ at }: { at?: string }) {
  if (!at || !formatDate(at)) return null;
  return (
    <time className={styles.time} dateTime={at} title={new Date(at).toLocaleString("ru-RU")} data-testid="message-time">
      {formatDate(at)}
    </time>
  );
}