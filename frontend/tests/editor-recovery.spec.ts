import { expect, test } from "@playwright/test";

import { cleanupProjects, clickText, createProject, REAL_STACK, sendMaterialsAndBrief, speedUp, startGeneration, uploadTemplate, waitForAllVariantsDone } from "./helpers";

test.describe("сохранность черновика", () => {
  test.afterEach(async ({ page }) => { await cleanupProjects(page); });

  test("переключение варианта, reload, ошибка запроса и повторное применение", async ({ page }) => {
    test.skip(REAL_STACK, "Инъекция ошибки предназначена для локального mock-конвейера");
    await speedUp(page);
    await createProject(page);
    await uploadTemplate(page);
    await sendMaterialsAndBrief(page);
    await startGeneration(page);
    await waitForAllVariantsDone(page);
    await expect(page.getByTestId("toggle-editor")).toBeVisible();
    await page.locator(".preview-stage").getByRole("button", { name: "заголовок" }).first().click({ force: true });
    await page.getByTestId("prop-text").fill("Сохранённый локальный заголовок");
    await expect(page.getByTestId("draft-status")).toContainText("сохранён в этом браузере");
    const stored = await page.evaluate(() => {
      const key = Object.keys(localStorage).find((key) => key.startsWith("pd.editor-draft.v1:") && key.endsWith(":compact:1"))!;
      return { key, value: localStorage.getItem(key)! };
    });
    const object = page.locator('[data-testid^="canvas-object-"]').filter({ hasText: "Сохранённый локальный заголовок" }).first();
    await page.getByTestId("variant-switch").locator("label").filter({ hasText: "Сбалансированный" }).click();
    await expect(page.getByTestId("draft-badge")).toHaveCount(0);
    await page.getByTestId("variant-switch").locator("label").filter({ hasText: "Компактный" }).click();
    await expect(object).toBeVisible();
    page.on("dialog", (dialog) => void dialog.accept());
    await page.reload();
    await expect(object).toBeVisible();
    await expect(page.getByTestId("draft-status")).toContainText("сохранён в этом браузере");
    await clickText(page, object);
    await expect(page.getByTestId("prop-text")).toHaveValue("Сохранённый локальный заголовок");

    // Laptop: all slide navigation remains reachable with the properties panel open.
    await page.setViewportSize({ width: 1280, height: 800 });
    await expect(page.locator(".editor-panel")).toHaveAttribute("data-open", "false");
    await expect(page.getByTestId("thumb-grip-0")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath("editor-laptop.png") });

    // Inject at fetch, before MSW: page.route cannot intercept service-worker requests.
    await page.evaluate(() => {
      const original = window.fetch.bind(window);
      let fail = true;
      window.fetch = async (...args) => {
        if (fail && String(args[0]).endsWith("/patches")) {
          fail = false;
          return new Response(JSON.stringify({ error: { code: "unavailable", message: "Тестовая ошибка сохранения" } }), { status: 503, headers: { "Content-Type": "application/json" } });
        }
        return original(...args);
      };
    });
    await page.getByTestId("editor-apply").click();
    await expect(page.getByTestId("draft-status")).toContainText("Тестовая ошибка сохранения");
    await expect(page.getByTestId("prop-text")).toHaveValue("Сохранённый локальный заголовок");
    await page.getByTestId("editor-apply").click();
    await expect(page.getByTestId("preview-pane")).toContainText("ревизия 2", { timeout: 30_000 });
    await expect(page.getByTestId("draft-badge")).toHaveCount(0);
    await page.getByTestId("download-menu").click();
    await expect(page.getByRole("menu")).toContainText("ревизия 2");
    const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("dl-pptx").click()]);
    expect(download.suggestedFilename()).toMatch(/-r2\.pptx$/);
    await page.reload();
    await expect(page.getByTestId("preview-pane")).toContainText("ревизия 2");
    await expect(page.getByTestId("draft-badge")).toHaveCount(0);
    // A draft from another client/revision must never overwrite the current PPTX.
    await page.evaluate(({ key, value }) => localStorage.setItem(key, value), stored);
    await page.reload();
    await expect(page.getByTestId("draft-badge")).toHaveCount(0);
    await page.getByTestId("older-draft").click();
    await expect(page.getByTestId("draft-status")).toContainText("автоматический перенос отключён");
    await expect(page.getByTestId("editor-apply-bar")).toBeDisabled();
    await page.getByTestId("latest-revision").click();
    await expect(page.getByTestId("draft-badge")).toHaveCount(0);
  });

  test("принятая правка после reload: ошибка задания не удаляет черновик", async ({ page }) => {
    test.skip(REAL_STACK, "Изолированная имитация асинхронной ошибки");
    await speedUp(page);
    await page.addInitScript(() => {
      const original = window.fetch.bind(window);
      window.fetch = async (...args) => {
        const url = String(args[0]);
        if (url.endsWith("/patches")) {
          sessionStorage.setItem("test-patch-accepted", "yes");
          return Response.json({ patch_job_id: "job_test_pending" }, { status: 202 });
        }
        if (url.endsWith("/jobs/job_test_pending")) {
          return Response.json({ job_id: "job_test_pending", status: sessionStorage.getItem("test-patch-fail") ? "failed" : "running", error: { message: "Ошибка сборки PPTX" } });
        }
        return original(...args);
      };
    });
    await createProject(page);
    await uploadTemplate(page);
    await sendMaterialsAndBrief(page);
    await startGeneration(page);
    await waitForAllVariantsDone(page);
    await expect(page.getByTestId("toggle-editor")).toBeVisible();
    await page.locator(".preview-stage").getByRole("button", { name: "заголовок" }).first().click({ force: true });
    await page.getByTestId("prop-text").fill("Не потерять при ошибке сборки");
    await page.getByTestId("editor-apply").click();
    await expect(page.getByTestId("draft-status")).toContainText("Применяем правки");
    await expect(page.getByTestId("prop-text")).toBeDisabled();
    page.on("dialog", (dialog) => void dialog.accept());
    await page.reload();
    await expect(page.getByTestId("draft-status")).toContainText("Применяем правки");
    await page.evaluate(() => sessionStorage.setItem("test-patch-fail", "yes"));
    await expect(page.getByTestId("draft-status")).toContainText("Ошибка сборки PPTX");
    await expect(page.getByTestId("editor-apply-bar")).toBeEnabled();
    await expect(page.getByTestId("slide-canvas")).toContainText("Не потерять при ошибке сборки");
  });
});