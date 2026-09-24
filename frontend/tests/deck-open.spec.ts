import { expect, test } from "@playwright/test";

const PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation";

test("opening a pptx as a deck says one line and asks nothing while the job is being created", async ({ page }) => {
  const project = { project_id: "deck-open-test", title: "Новая презентация", files: [] as Record<string, unknown>[], events: [] as Record<string, unknown>[], brief: {}, settings: {}, template_id: null as string | null, package_id: null as string | null, job_id: null as string | null };
  let release = () => {};
  const jobCreated = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: false } }));
  await page.route("**/api/projects/deck-open-test", async (r) => {
    if (r.request().method() === "PATCH") Object.assign(project, r.request().postDataJSON());
    await r.fulfill({ json: project });
  });
  await page.route("**/api/projects/deck-open-test/events", async (r) => {
    const event = { ...r.request().postDataJSON(), event_id: `event-${project.events.length}`, at: new Date().toISOString() };
    project.events.push(event);
    await r.fulfill({ json: event });
  });
  await page.route("**/api/projects/deck-open-test/events/*", (r) => r.fulfill({ json: {} }));
  await page.route("**/api/projects/deck-open-test/files", (r) => {
    project.files = [{ file_id: "file-deck", name: "Отчёт.pptx", size_bytes: 12, kind: "unassigned", check: { format: "pptx", status: "ok" } }];
    return r.fulfill({ json: project.files });
  });
  await page.route("**/api/projects/deck-open-test/files/file-deck", (r) => {
    Object.assign(project.files[0], r.request().postDataJSON());
    return r.fulfill({ json: project.files[0] });
  });
  await page.route("**/api/templates", (r) => r.request().method() === "POST"
    ? r.fulfill({ json: { template_id: "tpl-deck", job_id: "job-tpl", cached: false } })
    : r.fulfill({ json: [] }));
  await page.route("**/api/templates/tpl-deck", (r) => r.fulfill({ json: { template_id: "tpl-deck", name: "Отчёт.pptx", status: "queued" } }));
  await page.route("**/api/content", (r) => r.fulfill({ json: { package_id: "pkg-deck", job_id: "job-pkg", cached: false } }));
  // Задание создаётся не мгновенно: именно в этом окне раньше всплывал вопрос о режиме оформления.
  let posted: { settings?: { run_contextual_audit?: boolean } } | null = null;
  await page.route("**/api/generations", async (r) => {
    posted = r.request().postDataJSON();
    await jobCreated;
    await r.fulfill({ json: { job_id: "job_deck" } });
  });
  await page.route("**/api/generations/job_deck", (r) => r.fulfill({ json: {
    job_id: "job_deck", status: "running", stage: "compose", variants: [], created_at: new Date().toISOString(),
    metrics: {}, execution_mode: { mode: "real", layers: {} }, progress: { percent: 10, message: "Собираем слайды" },
    request: { schema_version: "1.2", template_id: "tpl-deck", package_id: "pkg-deck", settings: { variants: ["original"] } },
  } }));

  await page.goto("/project?id=deck-open-test");
  const chooser = page.waitForEvent("filechooser");
  await page.getByTestId("chat-attach").click();
  await (await chooser).setFiles({ name: "Отчёт.pptx", mimeType: PPTX_MIME, buffer: Buffer.from("deck") });
  await page.getByTestId("answer-deck").click();

  const chat = page.getByTestId("chat-list");
  await expect(chat).toContainText("Открываю «Отчёт.pptx» как готовую презентацию: слайды остаются как есть, править можно из чата или в редакторе.");
  await expect.poll(() => project.files[0]?.package_id).toBe("pkg-deck");
  // Пока задание создаётся, под «Открываю…» уже идёт таймер, справа — место редактора.
  const progress = chat.getByTestId("deck-progress");
  await expect(progress).toContainText("Открываю презентацию");
  await expect(page.getByTestId("office-pending")).toBeVisible();
  await page.waitForTimeout(800);
  await expect(chat).not.toContainText("Как оформить презентацию");
  await expect(page.getByTestId("chat-suggestions")).toHaveCount(0);

  const before = await progress.getByTestId("deck-progress-timer").textContent();
  release();
  // До сигнала редактора — только «Открываю…»: этапов и процентов разбора здесь нет.
  await expect.poll(() => project.job_id).toBe("job_deck");
  // Контекстный аудит при открытии выключен: его не просили, а воркер он держал минуты.
  expect(posted!.settings!.run_contextual_audit).toBe(false);
  await expect(progress).toContainText("Открываю презентацию");
  await expect(progress).not.toContainText("Собираем слайды");
  await expect(progress.getByTestId("deck-progress-percent")).toHaveCount(0);
  // Таймер не начинается заново, когда появилось задание.
  expect(Number((await progress.getByTestId("deck-progress-timer").textContent())!.split(":")[1])).toBeGreaterThanOrEqual(Number(before!.split(":")[1]));
  await expect(page.getByTestId("generation-progress")).toHaveCount(0);
  await expect.poll(() => project.job_id).toBe("job_deck");
  await expect(chat).not.toContainText("Как оформить презентацию");
  await expect(page.getByTestId("job-card")).toHaveCount(0);
  await expect(page.locator(".editor-panel-tabs [role=tab]")).toHaveText([/Чат/, /Файлы/]);
  // В ленте: приветствие, файл и одна фраза о том, что презентация открывается.
  await expect(chat.getByTestId("msg-assistant")).toHaveCount(2);
});

test("the editor warms up on «open as a deck», before the file finishes uploading", async ({ page }) => {
  const project = { project_id: "deck-warm-test", title: "Новая презентация", files: [] as Record<string, unknown>[], events: [] as Record<string, unknown>[], brief: {}, settings: {}, template_id: null, package_id: null, job_id: null };
  let finishUpload = () => {};
  const uploaded = new Promise<void>((resolve) => { finishUpload = resolve; });
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: true, script_url: "/warm-sdk.js" } }));
  await page.route("**/warm-sdk.js", (r) => r.fulfill({ contentType: "application/javascript", body: `
    window.DocsAPI = { DocEditor: function() {} };
    window.DocsAPI.DocEditor.warmUp = (id) => { window.__officeWarmedUp = id; };
  ` }));
  await page.route("**/api/projects/deck-warm-test", (r) => r.fulfill({ json: project }));
  await page.route("**/api/projects/deck-warm-test/events", (r) => r.fulfill({ json: { ...r.request().postDataJSON(), event_id: "e", at: new Date().toISOString() } }));
  await page.route("**/api/projects/deck-warm-test/files", async (r) => {
    await uploaded;
    await r.fulfill({ json: [{ file_id: "file-deck", name: "Отчёт.pptx", size_bytes: 12, kind: "unassigned", check: { format: "pptx", status: "ok" } }] });
  });
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.goto("/project?id=deck-warm-test");
  const chooser = page.waitForEvent("filechooser");
  await page.getByTestId("chat-attach").click();
  await (await chooser).setFiles({ name: "Отчёт.pptx", mimeType: PPTX_MIME, buffer: Buffer.from("deck") });
  await page.getByTestId("answer-deck").click();
  // Файл ещё едет на сервер, а скрипты ONLYOFFICE уже грузятся.
  await expect.poll(() => page.evaluate(() => (window as unknown as { __officeWarmedUp?: string }).__officeWarmedUp)).toBe("office-warmup");
  finishUpload();
});
