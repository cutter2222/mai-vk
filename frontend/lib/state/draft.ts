"use client";

/** Черновик новой генерации и последнее задание, чтобы вернуться к ним после перезагрузки. */

const LAST_JOB_KEY = "pd:last-job";

export function saveLastJob(jobId: string): void {
  try {
    window.localStorage.setItem(LAST_JOB_KEY, jobId);
  } catch {
    /* хранилище недоступно, продолжаем без него */
  }
}

export function loadLastJob(): string | null {
  try {
    return window.localStorage.getItem(LAST_JOB_KEY);
  } catch {
    return null;
  }
}
