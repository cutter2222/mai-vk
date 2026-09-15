import { setupWorker } from "msw/browser";

import { handlers } from "./handlers";

let started: Promise<void> | null = null;

/** Поднимает worker MSW один раз на вкладку. Вызывается только в сборке mock. */
export function startMocks(): Promise<void> {
  if (!started) {
    const worker = setupWorker(...handlers);
    started = worker
      .start({ onUnhandledRequest: "bypass", serviceWorker: { url: "/mockServiceWorker.js" }, quiet: true })
      .then(() => undefined);
  }
  return started;
}
