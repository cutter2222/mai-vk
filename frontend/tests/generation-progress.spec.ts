import { expect, test, type Page } from "@playwright/test";

async function setup(page: Page, initial: string, percent?: number) {
  const state = { status: initial, percent, available: false, error: 0 };
  const createdAt = new Date().toISOString();
  await page.route("**/api/projects/progress-test", (r) => r.fulfill({ json: {
    project_id: "progress-test", title: "Генерация", job_id: "job_progress", files: [], brief: {}, settings: {}, events: [],
  } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: true } }));
  await page.route("**/api/generations/job_progress", (r) => state.error
    ? r.fulfill({ status: state.error, json: { error: { code: `http_${state.error}`, message: "Connection unavailable" } } })
    : r.fulfill({ json: {
    job_id: "job_progress", status: state.status, stage: state.status === "running" ? "compose" : "queued",
    metrics: { totals: { duration_ms: 1000 } }, execution_mode: { mode: "real", layers: {} },
    created_at: createdAt, progress: { percent: state.percent, message: "Собираем слайды" },
    variants: state.available || state.status === "succeeded" ? [{ variant_id: "balanced", status: "ready", revision: 1, artifacts: { pptx: "balanced/r1/deck.pptx" } }] : [],
  } }));
  return state;
}

async function setupOffice(page: Page) {
  const doc = { id: "progress-doc", revision: 1, active_key: null, error: null };
  await page.route("**/api/office/documents", (r) => r.fulfill({ json: doc }));
  await page.route("**/api/office/documents/progress-doc", (r) => r.fulfill({ json: doc }));
  await page.route("**/api/office/documents/progress-doc/config", (r) => r.fulfill({ json: { script_url: "/progress-sdk.js", config: {} } }));
  await page.route("**/progress-sdk.js", (r) => r.fulfill({ contentType: "application/javascript", body: `
    window.DocsAPI = { DocEditor: function(id, config) {
      const frame = document.createElement('iframe'); document.getElementById(id).appendChild(frame);
      this.destroyEditor = () => frame.remove();
      this.requestClose = () => config.events.onRequestClose();
      setTimeout(() => config.events.onDocumentReady(), 0);
    }};
  ` }));
  await page.route("**/api/office/documents/progress-doc/objects/1", (r) => r.fulfill({ json: { revision: 1, objects: [] } }));
  await page.route("**/api/office/documents/progress-doc/preview/1", (r) => r.fulfill({ json: { revision: 1, slides: ["slide-01.png"], ratio: 16 / 9 } }));
  await page.route("**/slide-01.png", (r) => r.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4ZkAAAAASUVORK5CYII=", "base64") }));
}

test("server progress is visible, phrases rotate and completion opens generated slides", async ({ page }) => {
  const state = await setup(page, "running", 42);
  await setupOffice(page);
  await page.goto("/project?id=progress-test");
  const progress = page.getByTestId("generation-progress");
  await expect(progress).toContainText("Собираем слайды");
  await expect(progress.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
  const phrase = await page.getByTestId("generation-phrase").textContent();
  await expect(page.getByTestId("generation-phrase")).not.toHaveText(phrase!);
  state.status = "succeeded";
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
  await expect(progress).toHaveCount(0);
});

for (const initiallyOffline of [false, true]) {
  test(`connection errors retain progress and recover (initial=${initiallyOffline})`, async ({ page }) => {
    const state = await setup(page, "running", 42);
    if (initiallyOffline) state.error = 503;
    await page.goto("/project?id=progress-test");
    const progress = page.getByTestId("generation-progress");
    await expect(progress).toBeVisible();
    if (!initiallyOffline) {
      await expect(progress.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
      state.error = 503;
    }
    await expect(progress.getByRole("alert")).toContainText("Пробуем подключиться снова");
    state.error = 0;
    state.percent = 65;
    await expect(progress.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "65");
    await expect(progress.getByRole("alert")).toHaveCount(0);
  });
}

test("partial presentation keeps compact progress until completion", async ({ page }) => {
  const state = await setup(page, "running", 70);
  state.available = true;
  await setupOffice(page);
  await page.goto("/project?id=progress-test");
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
  const progress = page.locator(".office-slot").getByTestId("generation-progress");
  await expect(progress.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "70");
  state.status = "succeeded";
  await expect(progress).toHaveCount(0);
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
});

test("retry keeps the existing document while the next generation runs", async ({ page }) => {
  const state = await setup(page, "failed", 70);
  state.available = true;
  await setupOffice(page);
  await page.route("**/api/jobs/job_progress/retry", (r) => r.fulfill({ json: { job_id: "job_retry" } }));
  await page.route("**/api/generations/job_retry", (r) => r.fulfill({ json: {
    job_id: "job_retry", status: "running", stage: "compose", variants: [],
    created_at: new Date().toISOString(), progress: { percent: 15, message: "Новая сборка" },
    metrics: {}, execution_mode: { mode: "real", layers: {} },
  } }));
  await page.route("**/api/projects/progress-test", (r) => r.request().method() === "PATCH"
    ? r.fulfill({ json: {} }) : r.fallback());
  await page.route("**/api/projects/progress-test/events", (r) => r.fulfill({ json: {
    ...r.request().postDataJSON(), event_id: "retry-event", at: new Date().toISOString(),
  } }));
  await page.goto("/project?id=progress-test");
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
  await expect(page.getByTestId("generation-progress")).toHaveCount(0);
  await page.getByTestId("retry").click();
  await expect(page.locator(".office-slot").getByTestId("generation-progress")).toContainText("Новая сборка");
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
});

test("reduced motion disables progress animations", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await setup(page, "queued");
  await page.goto("/project?id=progress-test");
  const progress = page.getByTestId("generation-progress");
  await expect(progress.getByTestId("generation-phrase")).toHaveCSS("animation-name", "none");
  await expect(progress.getByRole("progressbar").locator("div")).toHaveCSS("animation-name", "none");
});

for (const status of [404, 410]) {
  test(`missing job ${status} does not promise automatic recovery`, async ({ page }) => {
    const state = await setup(page, "running");
    state.error = status;
    await page.goto("/project?id=progress-test");
    await expect(page.getByTestId("job-missing")).toContainText("Задание не найдено");
    await expect(page.getByTestId("generation-progress")).toHaveCount(0);
  });
}

test("queued job without server percentage uses indeterminate progress", async ({ page }) => {
  await setup(page, "queued");
  await page.goto("/project?id=progress-test");
  const progress = page.getByTestId("generation-progress");
  await expect(progress).toBeVisible();
  await expect(progress.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
});

for (const status of ["failed", "canceled"]) {
  test(`${status} stops the animation and shows a terminal state`, async ({ page }) => {
    const state = await setup(page, "running", 30);
    await page.goto("/project?id=progress-test");
    await expect(page.getByTestId("generation-progress")).toBeVisible();
    state.status = status;
    await expect(page.getByTestId("presentation-status")).toContainText(status === "failed" ? "Не удалось собрать" : "Генерация отменена");
    await expect(page.getByTestId("generation-progress")).toHaveCount(0);
  });
}