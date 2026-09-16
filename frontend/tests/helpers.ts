import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, type Page } from "@playwright/test";

/** Настоящие файлы из tests/fixtures: рабочий режим проверяет их на сервере, заглушки содержимое не читают. */
const FIXTURES = path.resolve(__dirname, "../../tests/fixtures");
export const fixture = (rel: string): Buffer => readFileSync(path.join(FIXTURES, rel));
export const PPTX = () => fixture("pptx/mini_template.pptx");
/** Копия шаблона с другим sha256: шаблоны дедуплицируются по байтам, а сценарий «вариант падает» завязан на имя. */
export const PPTX_FAIL = () => fixture("pptx/mini_template_fail.pptx");
export const XLSX = () => fixture("content/metrics.xlsx");
export const DOCX = () => fixture("content/product_description.docx");
export const DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

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

/** Создаёт проект с главной и возвращает его идентификатор. */
export async function createProject(page: Page): Promise<string> {
  await page.goto("/");
  await page.getByTestId("new-project").click();
  await page.waitForURL(/\/project\?id=/);
  await expect(page.getByTestId("project-editor")).toBeVisible();
  return new URL(page.url()).searchParams.get("id") as string;
}

/** Прикрепляет файлы через кнопку-скрепку: диалог выбора файла работает во всех трёх движках. */
export async function attach(page: Page, files: Array<{ name: string; mimeType: string; buffer: Buffer }>): Promise<void> {
  const [chooser] = await Promise.all([page.waitForEvent("filechooser"), page.getByTestId("chat-attach").click()]);
  await chooser.setFiles(files);
  await expect(page.getByTestId("pending-files")).toContainText(files[0].name);
}

export async function sendMessage(page: Page, text?: string): Promise<void> {
  if (text) await page.getByTestId("chat-input").fill(text);
  await page.getByTestId("chat-send").click();
}

/** Кладёт PPTX в чат и подтверждает, что это шаблон. */
export async function uploadTemplate(page: Page, name = "Корпоративный шаблон.pptx"): Promise<void> {
  await attach(page, [{ name, mimeType: PPTX_MIME, buffer: /fail/i.test(name) ? PPTX_FAIL() : PPTX() }]);
  await sendMessage(page);
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
  ).toBeVisible({ timeout: 50000 });
}
