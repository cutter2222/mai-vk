import { expect, test } from "@playwright/test";

// Только чтение существующих шаблонов: без загрузки, LLM, редакторских сессий и удаления.
test("живой шаблон: стиль, сведения о файле, слайды ONLYOFFICE и скачивание", async ({ page, request }) => {
  test.skip(!process.env.TEMPLATES_LIVE, "Нужен работающий локальный стек с шаблонами");
  const response = await request.get("/api/templates");
  expect(response.ok()).toBeTruthy();
  const items = await response.json();
  const template = items.find((item: { template_id: string; status: string }) => item.status === "succeeded"
    && (!process.env.TEMPLATE_ID || item.template_id === process.env.TEMPLATE_ID));
  expect(template).toBeTruthy();
  const errors: string[] = [];
  const sdkURLs: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (req) => { if (req.url().includes("sdkjs/slide/sdk-all.js")) sdkURLs.push(req.url()); });
  await page.goto("/templates");
  await expect(page.getByTestId(`template-card-${template.template_id}`)).toBeVisible();
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/templates-library.png`, fullPage: true });
  await page.getByTestId(`template-card-${template.template_id}`).click();
  await expect(page.getByRole("heading", { name: "Дизайн-система", exact: true })).toBeVisible();
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/templates-style.png`, fullPage: true });
  await expect(page.getByTestId("design-guidelines")).toBeVisible();
  await expect(page.getByTestId("section-patterns")).toHaveCount(0);
  await expect(page.getByTestId("section-source")).toContainText("Слайды");
  for (const section of ["details"]) {
    await page.getByTestId(`section-${section}`).click();
    await expect(page.getByTestId(`section-${section}`)).toHaveAttribute("aria-current", "page");
    if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/templates-${section}.png`, fullPage: true });
  }
  expect(sdkURLs).toHaveLength(0);
  await page.getByTestId("template-open-source").click();
  await expect(page.locator("iframe")).toBeVisible();
  await expect(page.getByTestId("office-loading")).toHaveCount(0, { timeout: 90_000 });
  await expect(page.getByText("Просмотр недоступен", { exact: true })).toHaveCount(0);
  await expect(page.locator(".tpl-section-heading")).toHaveCount(0);
  const content = await page.locator(".tpl-content").boundingBox();
  const frame = await page.locator("iframe").boundingBox();
  expect(frame).not.toBeNull();
  expect(content).not.toBeNull();
  expect(frame!.y).toBeCloseTo(content!.y, 0);
  expect(frame!.height).toBeCloseTo(content!.height, 0);
  expect(frame!.width).toBeCloseTo(content!.width, 0);
  expect(sdkURLs.length).toBeGreaterThan(0);
  const officeFrame = page.frameLocator("iframe");
  await expect(officeFrame.getByText("Введите имя, которое будет использоваться", { exact: false })).toHaveCount(0);
  await expect(officeFrame.getByRole("button", { name: /^Чат(?:\s|$)/ })).toHaveCount(0);
  await expect(officeFrame.getByRole("button", { name: "По размеру слайда", exact: true })).toHaveAttribute("aria-pressed", "true");
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/templates-onlyoffice.png`, fullPage: true });
  await page.setViewportSize({ width: 1024, height: 768 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByTestId("section-style").click();
  await expect(page.locator("iframe")).toHaveCount(0);
  const download = await request.get(`/api/templates/${template.template_id}/source`);
  expect(download.ok()).toBeTruthy();
  expect((await download.body()).subarray(0, 2).toString()).toBe("PK");
  expect(errors).toEqual([]);
});