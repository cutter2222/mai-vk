import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

import { collectConsoleErrors, fillBriefAndImport, speedUp, startGeneration, uploadTemplate, waitForAllVariantsDone } from "./helpers";

const SHOTS = process.env.SHOT_DIR;
const shot = async (page: Page, name: string) => {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
};

test.describe("сквозной сценарий на заглушках", () => {
  test("шаблон → бриф → генерация → варианты → аудит → исправление → ревизия → скачивание", async ({ page, browserName }) => {
    await speedUp(page, 8);
    const errors = collectConsoleErrors(page);

    await page.goto("/");
    await expect(page.getByTestId("health")).toContainText("Сервис работает");
    await uploadTemplate(page);
    await expect(page.getByTestId("generate")).toBeDisabled();

    await fillBriefAndImport(page);
    await expect(page.getByTestId("import-summary")).toContainText("фактов");
    await expect(page.getByTestId("template-profile")).toBeVisible({ timeout: 15000 });

    // Проверка границ диапазона слайдов
    await page.getByTestId("slides-min").fill("20");
    await expect(page.getByText("Минимум больше максимума")).toBeVisible();
    await expect(page.getByTestId("generate")).toBeDisabled();
    await page.getByTestId("slides-min").fill("10");
    await expect(page.getByTestId("generate")).toBeEnabled();

    const jobId = await startGeneration(page);
    expect(jobId).toMatch(/^job_/);
    await expect(page.getByTestId("progress-panel")).toBeVisible();
    await expect(page.getByTestId("execution-mode")).toContainText("Заглушки");
    await expect(page.getByTestId("variants-grid")).toBeVisible();
    await expect(page.locator('[data-testid^="variant-card-"]')).toHaveCount(3);

    // Файлы появляются раньше аудита
    await expect(page.getByTestId("download-compact")).toBeEnabled({ timeout: 40000 });
    await shot(page, "workspace-running");

    await waitForAllVariantsDone(page);
    await expect(page.getByTestId("metrics-panel")).toBeVisible();
    await shot(page, "workspace-done");

    // Скачивание PPTX: настоящий zip-пакет, а не JSON
    if (browserName === "chromium") {
      await page.getByTestId("download-compact").click();
      const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("dl-pptx-compact").click()]);
      expect(download.suggestedFilename()).toMatch(/\.pptx$/);
      const path = await download.path();
      expect(path).toBeTruthy();
      const head = readFileSync(path as string).subarray(0, 2).toString("latin1");
      expect(head).toBe("PK");
      await page.keyboard.press("Escape");
    }

    // Аудит варианта balanced: рамки, выбор, исправление, ревизия
    await page.getByTestId("variant-card-balanced").click();
    await page.getByTestId("layout-switch").getByText("По одному").click();
    await expect(page.getByTestId("audit-panel")).toBeVisible();
    await expect(page.getByTestId("audit-coverage")).toContainText("неполное");
    await page.getByTestId("issue-iss_1").click();
    await expect(page.getByTestId("issue-box-iss_1")).toBeVisible();
    await page.getByTestId("toggle-how-built").click();
    await expect(page.getByTestId("how-built")).toBeVisible();
    await shot(page, "workspace-audit");

    await page.getByTestId("issue-check-iss_1").check();
    await page.getByTestId("repair").click();
    await expect(page.getByText("Исправления применены")).toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId("revisions-panel")).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("audit-panel")).toContainText("ревизия 2");
    await expect(page.getByTestId("issue-iss_1")).toHaveCount(0);
    await shot(page, "workspace-revision");

    // Клавиатура листает слайды
    await page.locator('[data-testid="thumb-strip"] button').first().click();
    await expect(page.getByText(/Слайд 1 из/)).toBeVisible();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByText(/Слайд 2 из/)).toBeVisible();

    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("прямая ссылка после перезагрузки и «продолжить последнее задание»", async ({ page }) => {
    await speedUp(page, 8);
    await page.goto("/");
    await uploadTemplate(page);
    await fillBriefAndImport(page);
    const jobId = await startGeneration(page);
    await page.reload();
    await expect(page.getByTestId("progress-panel")).toContainText(jobId);
    await page.goto("/");
    await expect(page.getByTestId("continue-last")).toBeVisible();
  });

  test("неизвестное задание и /workspace без параметра", async ({ page }) => {
    await page.goto("/workspace?job=job_unknown");
    await expect(page.getByRole("heading", { name: "Задание не найдено" })).toBeVisible();
    await page.goto("/workspace");
    await expect(page.getByTestId("workspace-empty")).toBeVisible();
  });

  test("частичная ошибка варианта и повтор", async ({ page }) => {
    await speedUp(page, 8);
    await page.goto("/");
    await uploadTemplate(page, "Шаблон fail.pptx");
    await fillBriefAndImport(page);
    await startGeneration(page);
    await expect(page.getByTestId("variant-card-detailed").locator('[data-testid="status-failed"]')).toBeVisible({ timeout: 40000 });
    await waitForAllVariantsDone(page);
    await expect(page.getByTestId("progress-panel")).toContainText("частичный результат");
    await expect(page.getByTestId("download-compact")).toBeEnabled();
    await page.getByTestId("retry").click();
    await page.waitForURL(/\/workspace\?job=/);
    await expect(page.getByTestId("progress-panel")).toBeVisible();
  });

  test("отмена задания", async ({ page }) => {
    await speedUp(page, 2);
    await page.goto("/");
    await uploadTemplate(page);
    await fillBriefAndImport(page);
    await startGeneration(page);
    await page.getByTestId("cancel").click();
    await expect(page.getByTestId("progress-panel").locator('[data-testid="status-canceled"]')).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("retry")).toBeVisible();
  });

  test("конфликт ревизии при исправлении по устаревшему отчёту", async ({ page }) => {
    await speedUp(page, 8);
    await page.goto("/");
    await uploadTemplate(page);
    await fillBriefAndImport(page);
    const jobId = await startGeneration(page);
    await waitForAllVariantsDone(page);

    // Состояние заглушки живёт в памяти вкладки, поэтому устаревание имитируем запросом с неверной ревизией.
    const response = await page.evaluate(async (id) => {
      const r = await fetch(`/api/generations/${id}/variants/balanced/repairs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_revision: 99, issue_ids: ["iss_1"] }),
      });
      return { status: r.status, body: (await r.json()) as { error: { code: string } } };
    }, jobId);
    expect(response.status).toBe(409);
    expect(response.body.error.code).toBe("revision_stale");
  });
});
