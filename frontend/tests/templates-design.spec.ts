import { expect, test } from "@playwright/test";

// Проверка production-сборки с изолированными HTTP-фикстурами, без изменения библиотеки.
test("каталог: поиск, ошибка загрузки и восстановление", async ({ page }) => {
  let fail = true;
  await page.route("**/api/templates", (route) => route.fulfill(fail
    ? { status: 503, json: { error: { code: "unavailable", message: "Offline" } } }
    : { json: [{ template_id: "tpl-ui", name: "Фирменный стиль.pptx", status: "succeeded", created_at: "2026-09-21", colors: ["#0077FF"], slide_count: 3, pattern_count: 2 }] }));
  await page.goto("/templates");
  await expect(page.getByRole("button", { name: "Загрузить PPTX", exact: true })).toHaveCount(0);
  await expect(page.getByTestId("new-template")).toBeVisible();
  await expect(page.getByText("Библиотека недоступна", { exact: true })).toBeVisible();
  await expect(page.getByTestId("templates-empty")).toHaveCount(0);
  fail = false;
  await page.getByRole("button", { name: "Повторить", exact: true }).click();
  await expect(page.getByTestId("template-card-tpl-ui")).toBeVisible();
  await page.getByRole("textbox", { name: "Поиск шаблонов" }).fill("нет такого");
  await expect(page.getByText("По вашему запросу шаблонов нет.", { exact: false })).toBeVisible();
  await page.getByRole("textbox", { name: "Поиск шаблонов" }).fill("фирменный");
  await expect(page.getByTestId("template-card-tpl-ui")).toBeVisible();
});

test("разделы анализа не загружают SDK; исходник доступен только для просмотра", async ({ page }) => {
  const profile = JSON.parse(await (await import("node:fs/promises")).readFile("../contracts/examples/template_profile.example.json", "utf8"));
  let configs = 0;
  let sdkRequests = 0;
  await page.route("**/api/templates/tpl-ui", (route) => route.fulfill({ json: { status: "succeeded", name: "Дизайн-система.pptx", job_id: "ui", profile, previews: [] } }));
  await page.route("**/api/office/capabilities", (route) => route.fulfill({ json: { enabled: true } }));
  await page.route("**/api/office/templates/tpl-ui/config", (route) => {
    configs += 1;
    return route.fulfill({ json: { script_url: "/template-test-sdk.js", config: { editorConfig: { mode: "view" }, document: { permissions: { edit: false } } } } });
  });
  await page.route("**/template-test-sdk.js", (route) => {
    sdkRequests += 1;
    return route.fulfill({ contentType: "application/javascript", body: `window.DocsAPI = { DocEditor: function(id, config) {
      if (config.editorConfig.mode !== 'view' || config.document.permissions.edit) throw Error('not read only');
      const host = document.getElementById(id); const frame = document.createElement('iframe');
      frame.title = 'Просмотр шаблона'; frame.style.cssText = 'width:100%;height:100%'; host.replaceWith(frame);
      const timer = setTimeout(() => config.events.onDocumentReady(), 20);
      this.destroyEditor = () => { clearTimeout(timer); frame.replaceWith(host); };
    }};` });
  });
  await page.goto("/templates?id=tpl-ui");
  await expect(page.getByRole("heading", { name: "Дизайн-система", exact: true })).toBeVisible();
  const navigation = page.getByRole("navigation", { name: "Разделы шаблона" });
  await expect(navigation.getByRole("button")).toHaveText(["Стиль", /^Слайды/, "О файле"]);
  await expect(page.getByTestId("section-slides")).toHaveCount(0);
  await expect(page.getByTestId("section-patterns")).toHaveCount(0);
  await expect(page.getByTestId("design-sketch")).toBeVisible();
  await expect(page.getByTestId("design-fixed")).toBeVisible();
  await expect(page.getByTestId("design-guidelines")).toBeVisible();
  await expect(page.getByTestId("structure-stats")).toHaveCount(0);
  await page.getByTestId("section-details").click();
  await expect(page.getByRole("heading", { name: "О файле", exact: true })).toBeVisible();
  await expect(page.getByTestId("structure-stats")).toBeVisible();
  await expect(page.getByTestId("digest-meta")).toBeVisible();
  await expect(page.getByTestId("design-guidelines")).toHaveCount(0);
  expect(configs).toBe(0);
  expect(sdkRequests).toBe(0);
  await page.getByTestId("template-open-source").click();
  await expect(page.getByTitle("Просмотр шаблона", { exact: true })).toBeVisible();
  await expect(page.getByTestId("office-loading")).toHaveCount(0);
  expect(configs).toBe(1);
  await expect(page.locator(".tpl-section-heading")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Слайды", exact: true })).toHaveCount(0);
  await expect(page.getByText("Просмотр в ONLYOFFICE", { exact: false })).toHaveCount(0);
  for (const viewport of [{ width: 1440, height: 900 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    const content = await page.locator(".tpl-content").boundingBox();
    const frame = await page.getByTitle("Просмотр шаблона", { exact: true }).boundingBox();
    expect(content).not.toBeNull();
    expect(frame).not.toBeNull();
    expect(frame!.x).toBeCloseTo(content!.x, 0);
    expect(frame!.y).toBeCloseTo(content!.y, 0);
    expect(frame!.width).toBeCloseTo(content!.width, 0);
    expect(frame!.height).toBeCloseTo(content!.height, 0);
    expect(frame!.y + frame!.height).toBeCloseTo(viewport.height, 0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "К стилю", exact: true }).click();
  await expect(page.getByTestId("section-style")).toHaveAttribute("aria-current", "page");
  await expect(page.locator("iframe")).toHaveCount(0);
  await page.getByTestId("section-source").click();
  await expect(page.getByTitle("Просмотр шаблона", { exact: true })).toBeVisible();
  expect(configs).toBe(2);
  expect(sdkRequests).toBe(1);
  await page.getByTestId("section-style").click();
  await page.route("**/api/office/templates/tpl-ui/config", (route) => route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "Просмотр временно недоступен" } } }));
  await page.getByTestId("section-source").click();
  await expect(page.getByText("Просмотр недоступен", { exact: true })).toBeVisible();
  await page.getByTestId("section-style").click();
  await expect(page.getByRole("heading", { name: "Дизайн-система", exact: true })).toBeVisible();
  await page.setViewportSize({ width: 1024, height: 768 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("новый PPTX открывает анализ, выключенный ONLYOFFICE не мешает библиотеке", async ({ page }) => {
  await page.route("**/api/templates", async (route) => {
    if (route.request().method() === "POST") return route.fulfill({ status: 202, json: { template_id: "tpl-upload", job_id: "ui", cached: false } });
    return route.fulfill({ json: [] });
  });
  await page.route("**/api/templates/tpl-upload", (route) => route.fulfill({ json: { status: "running", name: "Новый.pptx", job_id: "ui", previews: [] } }));
  await page.route("**/api/office/capabilities", (route) => route.fulfill({ json: { enabled: false } }));
  await page.goto("/templates");
  const chooser = page.waitForEvent("filechooser");
  await page.getByTestId("new-template").click();
  await (await chooser).setFiles({ name: "Новый.pptx", mimeType: "application/vnd.openxmlformats-officedocument.presentationml.presentation", buffer: Buffer.from("PPTX fixture") });
  await expect(page).toHaveURL(/templates\?id=tpl-upload/);
  await expect(page.getByTestId("template-analyzing")).toBeVisible();
  await expect(page.getByTestId("template-open-source")).toHaveCount(0);
  await page.getByTestId("template-actions").click();
  await expect(page.getByRole("menuitem", { name: "Скачать исходный PPTX" })).toHaveAttribute("href", "/api/templates/tpl-upload/source");
});

test("готовый шаблон без ONLYOFFICE сохраняет стиль и сведения о файле", async ({ page }) => {
  const profile = JSON.parse(await (await import("node:fs/promises")).readFile("../contracts/examples/template_profile.example.json", "utf8"));
  await page.route("**/api/templates/tpl-ui", (route) => route.fulfill({ json: { status: "succeeded", name: "Шаблон.pptx", profile, previews: [] } }));
  await page.route("**/api/office/capabilities", (route) => route.fulfill({ json: { enabled: false } }));
  await page.goto("/templates?id=tpl-ui");
  await expect(page.getByTestId("design-guidelines")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Разделы шаблона" }).getByRole("button")).toHaveText(["Стиль", "О файле"]);
  await expect(page.getByTestId("template-open-source")).toHaveCount(0);
  await page.getByTestId("section-details").click();
  await expect(page.getByTestId("structure-assets")).toBeVisible();
  await page.getByTestId("template-actions").click();
  await expect(page.getByRole("menuitem", { name: "Скачать исходный PPTX" })).toHaveAttribute("href", "/api/templates/tpl-ui/source");
});