import { expect, test, type Page } from "@playwright/test";

/** deck — «открыть как презентацию»: один PPTX стал и шаблоном, и содержанием, вариант original. */
async function setup(page: Page, initial: string, percent?: number, deck = false) {
  const state = { status: initial, percent, available: false, error: 0, revision: 1, warnings: [] as { code: string; message: string }[] };
  const createdAt = new Date().toISOString();
  const variantId = deck ? "original" : "balanced";
  const files = deck ? [{ file_id: "file_deck", name: "Отчёт.pptx", size_bytes: 12, kind: "template", template_id: "tpl_deck", package_id: "pkg_deck", check: { status: "ok", format: "pptx" } }] : [];
  // Готовая презентация в чате — ответ «Открыть как презентацию» на вопрос о файле.
  const events = deck ? [{ event_id: "deck-answer", at: createdAt, role: "assistant", kind: "template_question", file_id: "file_deck", resolved: "deck" }] : [];
  await page.route("**/api/projects/progress-test", (r) => r.fulfill({ json: {
    project_id: "progress-test", title: "Генерация", job_id: "job_progress", files, brief: {}, settings: {}, events,
    ...(deck ? { template_id: "tpl_deck", package_id: "pkg_deck" } : {}),
  } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: true, ...(deck ? { script_url: "/progress-sdk.js" } : {}) } }));
  // Готовая презентация: число слайдов известно сразу, файл публикуется раньше рендера (ready_at).
  const deckVariant = () => state.available || state.status === "succeeded"
    ? [{ variant_id: "original", status: state.status === "succeeded" ? "ready" : "running", revision: state.revision, slide_count: 12, ready_at: createdAt, artifacts: { pptx: `original/r${state.revision}/deck.pptx` } }]
    : [{ variant_id: "original", status: "running", revision: 1, slide_count: 12 }];
  await page.route("**/api/generations/job_progress", (r) => state.error
    ? r.fulfill({ status: state.error, json: { error: { code: `http_${state.error}`, message: "Connection unavailable" } } })
    : r.fulfill({ json: {
    job_id: "job_progress", status: state.status, stage: state.status === "running" ? "compose" : "queued",
    metrics: { totals: { duration_ms: 1000 } }, execution_mode: { mode: "real", layers: {} },
    versions: { app: "test", contracts: "1.11", skills: [], prompts: [], models: [] },
    created_at: createdAt, progress: { percent: state.percent, message: "Собираем слайды" }, warnings: state.warnings,
    ...(deck ? { request: { schema_version: "1.2", template_id: "tpl_deck", package_id: "pkg_deck", settings: { variants: ["original"], run_contextual_audit: false } } } : {}),
    variants: deck ? deckVariant() : state.available || state.status === "succeeded" ? [{ variant_id: variantId, status: "ready", revision: 1, artifacts: { pptx: `${variantId}/r1/deck.pptx` } }] : [],
  } }));
  return state;
}

/** revision — версия офисного документа: 0 — его ещё не правили и не сохраняли. */
async function setupOffice(page: Page, revision = 1) {
  const doc = { id: "progress-doc", revision, active_key: null, error: null };
  const created: string[] = [];
  await page.route("**/api/office/documents", (r) => {
    created.push(r.request().postDataJSON()?.artifact ?? "");
    return r.fulfill({ json: doc });
  });
  await page.route("**/api/office/documents/progress-doc", (r) => r.fulfill({ json: doc }));
  await page.route("**/api/office/documents/progress-doc/config", (r) => r.fulfill({ json: { script_url: "/progress-sdk.js", config: {} } }));
  await page.route("**/progress-sdk.js", (r) => r.fulfill({ contentType: "application/javascript", body: `
    window.DocsAPI = { DocEditor: function(id, config) {
      const frame = document.createElement('iframe'); document.getElementById(id).appendChild(frame);
      this.destroyEditor = () => frame.remove();
      this.requestClose = () => config.events.onRequestClose();
      window.__officeEvents = config.events;
      setTimeout(() => config.events.onDocumentReady(), 0);
    }};
    window.DocsAPI.DocEditor.warmUp = (id) => { window.__officeWarmedUp = id; };
  ` }));
  await page.route(`**/api/office/documents/progress-doc/objects/${revision}`, (r) => r.fulfill({ json: { revision, objects: [] } }));
  await page.route(`**/api/office/documents/progress-doc/preview/${revision}`, (r) => r.fulfill({ json: { revision, slides: ["slide-01.png"], ratio: 16 / 9 } }));
  await page.route("**/slide-01.png", (r) => r.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4ZkAAAAASUVORK5CYII=", "base64") }));
  return { doc, created };
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

test("opening a deck shows compact progress with a timer in the chat, not on top", async ({ page }) => {
  const state = await setup(page, "running", 5, true);
  await setupOffice(page);
  await page.goto("/project?id=progress-test");
  // Ход открытия — строкой под «Открываю…»; справа сразу место редактора, полосы сверху нет.
  // До сигнала редактора — только «Открываю…»: этапов и процентов разбора там нет.
  const progress = page.getByTestId("chat-list").getByTestId("deck-progress");
  await expect(progress).toContainText("Открываю презентацию");
  await expect(progress).not.toContainText("Собираем слайды");
  await expect(progress.getByTestId("deck-progress-percent")).toHaveCount(0);
  await expect(page.getByTestId("generation-progress")).toHaveCount(0);
  await expect(page.getByTestId("office-pending")).toBeVisible();
  await expect(page.locator(".preview-empty")).toHaveCount(0);
  // Справа — место под каждый из 12 слайдов, пока файл готовится.
  await expect(page.getByTestId("slide-skeletons").locator(".slide-skeleton")).toHaveCount(12);
  await expect(page.getByTestId("office-pending")).toContainText("Готовлю 12 слайдов к показу");
  // Редактор прогрет заранее штатным warmUp ONLYOFFICE.
  await expect.poll(() => page.evaluate(() => (window as unknown as { __officeWarmedUp?: string }).__officeWarmedUp)).toBe("office-warmup");
  const timer = progress.getByTestId("deck-progress-timer");
  const first = await timer.textContent();
  await expect(timer).not.toHaveText(first!, { timeout: 3000 });
  expect((await progress.boundingBox())!.height).toBeLessThan(48);
  // Файл опубликован раньше рендера: редактор открывается, пока задание ещё идёт, и
  // строка превращается в итог — фоновый разбор её не держит.
  state.available = true;
  state.percent = 60;
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
  const opened = page.getByTestId("chat-list").getByTestId("deck-opened");
  await expect(opened).toContainText("Презентация открыта за");
  await expect(progress).toHaveCount(0);
  await expect(page.getByTestId("generation-progress")).toHaveCount(0);
  // Разбор в фоне — тихой строкой с процентом задания, без полосы и без блокировки.
  const background = page.getByTestId("chat-list").getByTestId("deck-background");
  await expect(background).toContainText("Собираем слайды");
  await expect(background.getByTestId("deck-background-percent")).toHaveText("60%");
  await expect(background.getByRole("progressbar")).toHaveCount(0);
  state.status = "succeeded";
  await expect(background).toHaveCount(0);
  // Открытая как есть презентация: в шапке нет состояния задания — ни «Готово», ни отмены.
  await expect(page.locator(".editor-header")).not.toContainText("Готово");
  await expect(page.getByTestId("cancel")).toHaveCount(0);
  // Итог остаётся в истории чата и после конца задания.
  await expect(opened).toBeVisible();
  await expect(page.getByTestId("job-card")).toHaveCount(0);
});

test("charts revision opens by itself while the deck is untouched, and the chat says so", async ({ page }) => {
  const state = await setup(page, "running", 40, true);
  state.available = true;
  const office = await setupOffice(page, 0);
  await page.goto("/project?id=progress-test");
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
  await expect.poll(() => office.created).toEqual(["original/r1/deck.pptx"]);
  // Фон сделал диаграммы редактируемыми ревизией r2: документ никто не правил — редактор
  // открывает её сам, фраза в чате появляется сразу, а не в конце разбора.
  state.revision = 2;
  state.warnings = [{ code: "chart_images", message: "Сделал диаграммы редактируемыми: 7 (слайды 45–51)." }];
  await expect.poll(() => office.created).toEqual(["original/r1/deck.pptx", "original/r2/deck.pptx"]);
  await expect(page.getByTestId("chat-list").getByTestId("deck-charts")).toHaveText("Сделал диаграммы редактируемыми: 7 (слайды 45–51).");
  await expect(page.getByText("Доступна другая версия презентации")).toHaveCount(0);
  state.status = "succeeded";
  await expect(page.getByTestId("chat-list").getByTestId("deck-background")).toHaveCount(0);
  // О диаграммах сказано один раз: карточка задания их не повторяет.
  await expect(page.getByTestId("job-card")).toHaveCount(0);
  await expect(page.getByTestId("chat-list").getByTestId("deck-charts")).toHaveCount(1);
});

test("an edited deck keeps its document and offers the charts revision with a banner", async ({ page }) => {
  const state = await setup(page, "running", 40, true);
  state.available = true;
  const office = await setupOffice(page, 0);
  await page.goto("/project?id=progress-test");
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
  await expect.poll(() => office.created).toEqual(["original/r1/deck.pptx"]);
  // Человек начал править в ONLYOFFICE: новая ревизия его документ не подменяет.
  await page.evaluate(() => (window as unknown as { __officeEvents: { onDocumentStateChange: (e: { data: boolean }) => void } }).__officeEvents.onDocumentStateChange({ data: true }));
  state.revision = 2;
  state.warnings = [{ code: "chart_images", message: "Сделал диаграммы редактируемыми: 7 (слайды 45–51)." }];
  await expect(page.getByText("Доступна другая версия презентации")).toBeVisible();
  await expect(page.getByTestId("chat-list").getByTestId("deck-charts")).toBeVisible();
  expect(office.created).toEqual(["original/r1/deck.pptx"]);
});

test("opening a deck keeps the chat quiet until something needs attention", async ({ page }) => {
  const state = await setup(page, "running", 5, true);
  await page.goto("/project?id=progress-test");
  await expect(page.getByTestId("deck-progress")).toBeVisible();
  await expect(page.locator(".editor-panel-tabs [role=tab]")).toHaveText([/Чат/, /Файлы/]);
  // Полоса сверху уже говорит, что идёт сборка: в ленте её не повторяем.
  await expect(page.getByTestId("job-card")).toHaveCount(0);
  state.status = "succeeded";
  await expect(page.getByTestId("deck-opened")).toContainText("Презентация открыта за");
  await expect(page.getByTestId("deck-progress")).toHaveCount(0);
  await expect(page.getByTestId("job-card")).toHaveCount(0);
});

test("a deck that failed to open says so in the chat", async ({ page }) => {
  const state = await setup(page, "running", 5, true);
  await page.goto("/project?id=progress-test");
  await expect(page.getByTestId("job-card")).toHaveCount(0);
  state.status = "failed";
  await expect(page.getByTestId("job-summary")).toContainText("Не открыл презентацию");
});

test("a shown deck whose background parse failed says chat edits are off and offers a retry", async ({ page }) => {
  const state = await setup(page, "running", 40, true);
  state.available = true;
  await setupOffice(page);
  let retried = false;
  await page.route("**/api/jobs/job_progress/retry", (r) => { retried = true; return r.fulfill({ json: { job_id: "job_progress" } }); });
  await page.goto("/project?id=progress-test");
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
  state.status = "failed";
  const summary = page.getByTestId("job-summary");
  await expect(summary).toContainText("Слайды открыты, но разбор презентации не завершился");
  await expect(summary).toContainText("Правки из чата недоступны — редактор и скачивание работают");
  await page.getByTestId("deck-retry").click();
  await expect.poll(() => retried).toBe(true);
});

test("a deck opened without the model check offers it in the job details", async ({ page }) => {
  const state = await setup(page, "succeeded", 100, true);
  state.available = true;
  await setupOffice(page);
  let posted: { settings?: { run_contextual_audit?: boolean; variants?: string[] }; idempotency_key?: string } | null = null;
  await page.route("**/api/generations", (r) => {
    posted = r.request().postDataJSON();
    return r.fulfill({ json: { job_id: "job_progress" } });
  });
  await page.route("**/api/projects/progress-test", (r) => r.request().method() === "PATCH" ? r.fulfill({ json: {} }) : r.fallback());
  await page.route("**/api/projects/progress-test/events", (r) => r.fulfill({ json: { ...r.request().postDataJSON(), event_id: "audit-event", at: new Date().toISOString() } }));
  await page.goto("/project?id=progress-test");
  await expect(page.getByTestId("deck-opened")).toBeVisible();
  await page.getByTestId("deck-details").click();
  await page.getByTestId("deck-recheck").click();
  await expect.poll(() => posted?.settings?.run_contextual_audit).toBe(true);
  expect(posted!.settings!.variants).toEqual(["original"]);
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