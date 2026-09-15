import { ApiError, type ApiErrorBody } from "./api/client";

/**
 * Скачивание артефакта через fetch: ошибка API показывается пользователю, а не открывается пустой вкладкой;
 * в режиме заглушек запрос проходит через service worker MSW, чего не делает обычная навигация по ссылке.
 */
export async function downloadArtifact(url: string, fallbackName: string): Promise<void> {
  const response = await fetch(url);
  if (!response.ok) {
    let body: ApiErrorBody | undefined;
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      body = undefined;
    }
    throw new ApiError(response.status, body, `Не удалось скачать файл (${response.status})`);
  }
  const blob = await response.blob();
  const name = filenameFromDisposition(response.headers.get("Content-Disposition")) ?? fallbackName;
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 10_000);
}

function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null;
  const utf8 = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8) return decodeURIComponent(utf8[1]);
  const plain = header.match(/filename="?([^";]+)"?/i);
  return plain ? plain[1] : null;
}
