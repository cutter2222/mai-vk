import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

/** Настоящие файлы из tests/fixtures: рабочий режим проверяет их на сервере, заглушки содержимое не читают. */
const FIXTURES = path.resolve(__dirname, "../../tests/fixtures");
export const fixture = (rel: string): Buffer => readFileSync(path.join(FIXTURES, rel));
export const PPTX = () => fixture("pptx/mini_template.pptx");
/** Копия шаблона с другим sha256: шаблоны дедуплицируются по байтам, а сценарий «вариант падает» завязан на имя. */
export const PPTX_FAIL = () => fixture("pptx/mini_template_fail.pptx");
export const XLSX = () => fixture("content/metrics.xlsx");
export const DOCX = () => fixture("content/product_description.docx");
export const DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

/**
 * Рабочий режим (PLAYWRIGHT_BASE_URL — поднятый стек) строит планы вариантов настоящей моделью:
 * три браузера запускают три задания одновременно, девять вариантов делят трёх воркеров,
 * поэтому ожидания файлов и завершения на порядок дольше, чем у заглушек.
 */
export const REAL_STACK = Boolean(process.env.PLAYWRIGHT_BASE_URL);
export const WAIT = REAL_STACK
  ? { files: 300_000, variantsDone: 360_000, audit: 60_000, variantFailed: 300_000 }
  : { files: 40_000, variantsDone: 50_000, audit: 15_000, variantFailed: 40_000 };

/** Ускоряет имитацию заданий в заглушке в N раз (mocks/state.ts читает mock_speed). */
export async function speedUp(page: Page, factor = 8): Promise<void> {
  await page.addInitScript((f) => window.localStorage.setItem("mock_speed", String(f)), factor);
}

export function collectConsoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" && !/favicon/.test(m.text())) errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

export const PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation";
const XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

export const BRIEF_TEXT = "Сделай презентацию про запуск сервиса умных уведомлений для руководителей, чтобы одобрили расширение пилота";

/** Аннотация теста с идентификатором созданного проекта: afterEach удаляет их через API. */
export const PROJECT_ANNOTATION = "project";

/** Запоминает проект из адреса текущей страницы, чтобы afterEach удалил его. */
export function rememberProjectFromUrl(page: Page): string | null {
  const id = new URL(page.url()).searchParams.get("id");
  if (id) test.info().annotations.push({ type: PROJECT_ANNOTATION, description: id });
  return id;
}

/** Создаёт проект с главной и возвращает его идентификатор. */
export async function createProject(page: Page): Promise<string> {
  await page.goto("/");
  await page.getByTestId("new-project").click();
  await page.waitForURL(/\/project\?id=/);
  await expect(page.getByTestId("project-editor")).toBeVisible();
  return rememberProjectFromUrl(page) as string;
}

/**
 * Удаляет проекты теста: созданные через createProject и открытый в адресе страницы
 * (например, созданный редиректом /workspace?job=…). В рабочем режиме запрос уходит на сервер,
 * чтобы прогоны не оставляли тестовых проектов; в режиме заглушек статический сервер отвечает 404,
 * и это не ошибка — состояние заглушки живёт во вкладке.
 */
export async function cleanupProjects(page: Page): Promise<void> {
  const ids = new Set(
    test
      .info()
      .annotations.filter((a) => a.type === PROJECT_ANNOTATION && a.description)
      .map((a) => a.description as string),
  );
  const fromUrl = page.url().match(/[?&]id=(prj_[A-Za-z0-9]+)/);
  if (fromUrl) ids.add(fromUrl[1]);
  for (const id of ids) {
    try {
      await page.request.delete(`/api/projects/${encodeURIComponent(id)}`);
    } catch {
      // страница закрыта или сервер недоступен: уборка не должна ронять тест
    }
  }
}

/** Прикрепляет файлы через кнопку-скрепку: диалог выбора файла работает во всех трёх движках.
 *  PPTX не ждёт отправки: вопрос «шаблон или материал» появляется сразу; остальные файлы остаются вложениями. */
export async function attach(page: Page, files: Array<{ name: string; mimeType: string; buffer: Buffer }>): Promise<void> {
  const [chooser] = await Promise.all([page.waitForEvent("filechooser"), page.getByTestId("chat-attach").click()]);
  await chooser.setFiles(files);
  const first = files[0];
  if (/\.pptx$/i.test(first.name)) await expect(page.locator('[data-testid^="template-question-"]').last()).toContainText(first.name);
  else await expect(page.getByTestId("pending-files")).toContainText(first.name);
}

export async function sendMessage(page: Page, text?: string): Promise<void> {
  if (text) await page.getByTestId("chat-input").fill(text);
  await page.getByTestId("chat-send").click();
}

/** Кладёт PPTX в чат и подтверждает, что это шаблон: ответить можно, пока файл ещё грузится. */
export async function uploadTemplate(page: Page, name = "Корпоративный шаблон.pptx"): Promise<void> {
  await attach(page, [{ name, mimeType: PPTX_MIME, buffer: /fail/i.test(name) ? PPTX_FAIL() : PPTX() }]);
  await page.getByTestId("answer-template").last().click();
  await expect(page.getByTestId("template-card").last()).toBeVisible();
}

/** Материалы и задача одной фразой: карточки материалов и брифа. */
export async function sendMaterialsAndBrief(page: Page, text = BRIEF_TEXT): Promise<void> {
  await attach(page, [{ name: "metrics.xlsx", mimeType: XLSX_MIME, buffer: XLSX() }]);
  await sendMessage(page, text);
  await expect(page.getByTestId("import-summary").last()).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId("brief-card").last()).toBeVisible();
}

/** Открывает редактор брифа из карточки. Лента чата перерисовывается, когда приходит профиль шаблона
 *  или итог импорта, и клик по кнопке в этот момент может потеряться — повторяем до появления окна. */
export async function openBriefEditor(page: Page): Promise<void> {
  const tab = page.getByRole("tab", { name: "Параметры генерации" });
  for (let attempt = 0; attempt < 3; attempt++) {
    await page.getByTestId("edit-brief").last().click();
    const opened = await tab.waitFor({ state: "visible", timeout: 5000 }).then(() => true, () => false);
    if (opened) return;
  }
  throw new Error("редактор брифа не открылся после трёх попыток");
}

/** Запускает генерацию из карточки брифа и возвращает идентификатор задания. */
export async function startGeneration(page: Page): Promise<string> {
  await page.getByTestId("generate").last().click();
  const panel = page.getByTestId("progress-panel").last();
  await expect(panel).toBeVisible({ timeout: 15000 });
  const text = await panel.textContent();
  const jobId = text?.match(/job_[a-z0-9]+/)?.[0];
  expect(jobId).toBeTruthy();
  return jobId as string;
}

export async function waitForAllVariantsDone(page: Page): Promise<void> {
  const panel = page.getByTestId("progress-panel").last();
  await expect(
    panel.locator('[data-testid="status-needs_review"], [data-testid="status-succeeded"], [data-testid="status-failed"]').first(),
  ).toBeVisible({ timeout: WAIT.variantsDone });
}
