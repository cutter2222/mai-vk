import { expect, type Page } from "@playwright/test";

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

const PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation";
const XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

export async function uploadTemplate(page: Page, name = "Корпоративный шаблон.pptx"): Promise<void> {
  // Через диалог выбора файла: так работает во всех трёх движках, включая WebKit.
  const [chooser] = await Promise.all([page.waitForEvent("filechooser"), page.getByTestId("template-dropzone").click()]);
  await chooser.setFiles({ name, mimeType: PPTX_MIME, buffer: Buffer.from("PK-mock-template") });
  await expect(page.locator('[data-testid^="template-card-"]').filter({ hasText: name })).toBeVisible();
}

export async function fillBriefAndImport(page: Page): Promise<void> {
  await page.getByTestId("brief-title").fill("Запуск сервиса умных уведомлений");
  const [chooser] = await Promise.all([page.waitForEvent("filechooser"), page.getByTestId("content-dropzone").click()]);
  await chooser.setFiles([{ name: "metrics.xlsx", mimeType: XLSX_MIME, buffer: Buffer.from("mock") }]);
  await expect(page.getByText("metrics.xlsx")).toBeVisible();
  await page.getByTestId("content-submit").click();
  await expect(page.getByTestId("import-summary")).toBeVisible({ timeout: 15000 });
}

export async function startGeneration(page: Page): Promise<string> {
  await page.getByTestId("generate").click();
  await page.waitForURL(/\/workspace\?job=/);
  const url = new URL(page.url());
  return url.searchParams.get("job") as string;
}

export async function waitForAllVariantsDone(page: Page): Promise<void> {
  const panel = page.getByTestId("progress-panel");
  await expect(
    panel.locator('[data-testid="status-needs_review"], [data-testid="status-succeeded"], [data-testid="status-failed"]').first(),
  ).toBeVisible({ timeout: 50000 });
}
