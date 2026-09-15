"use client";

/**
 * Байты файлов проекта живут только в памяти вкладки: localStorage не вмещает документы.
 * После перезагрузки метаданные остаются в проекте, а уже загруженное на сервер (шаблон, пакет) не требует байтов.
 */
const files = new Map<string, File>();

export const fileStore = {
  put: (id: string, file: File) => files.set(id, file),
  get: (id: string) => files.get(id),
  has: (id: string) => files.has(id),
  remove: (id: string) => files.delete(id),
};
