import { expect, test, type Page } from "@playwright/test";

// Загрузка файлов в ленту не пишется: ни пузыря с именем файла, ни «Прочитал…», ни карточки
// задачи, когда презентация уже есть. Об упавшем разборе чат всё же говорит.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC", "base64");
const PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation";

const file = (file_id: string, name: string, format: string, kind = "material") => ({
  schema_version: "1.2", file_id, name, size_bytes: 2048, sha256: file_id.padEnd(64, "0"), mime: "application/octet-stream",
  kind, added_at: "2026-09-24T10:00:00Z", check: { status: "ok", format },
});

async function setup(page: Page, { importStatus = "succeeded" }: { importStatus?: "succeeded" | "failed" } = {}) {
  const state = { imports: 0 };
  await page.route("**/api/projects/quiet-test", (r) => r.fulfill({ json: {
    schema_version: "1.5", project_id: "quiet-test", title: "Тихая загрузка", template_id: "tpl_quiet", package_id: "pkg_old", job_id: "job_quiet", chosen_variant: null,
    created_at: "2026-09-24T10:00:00Z", updated_at: "2026-09-24T10:00:00Z", brief: { purpose: "", title: "", audience: "", goal: "", language: "ru", tone: "", must_include: [], avoid: [] },
    settings: { mode: "range", min: 10, max: 15, exact: 12, variants: ["compact"], contextual: true, images: false, seed: null, force: false },
    files: [file("file_old", "старое.png", "image")],
    // Запись о загрузке из старой истории: в ленте её больше нет.
    events: [{ event_id: "evt_1", at: "2026-09-24T10:00:01Z", role: "user", kind: "message", text: "", file_ids: ["file_old"] }],
  } }));
  await page.route("**/api/projects/quiet-test/events", (r) => r.fulfill({ json: { ...r.request().postDataJSON(), event_id: `evt_${Date.now()}`, at: new Date().toISOString() } }));
  await page.route("**/api/projects/quiet-test/files", (r) => r.fulfill({ status: 201, json: [file("file_new", "фото.png", "image")] }));
  await page.route("**/api/projects/quiet-test/files/*", (r) => r.fulfill({ json: file("file_new", "фото.png", "image") }));
  await page.route("**/api/content", (r) => { state.imports++; return r.fulfill({ status: 202, json: { package_id: "pkg_new", job_id: "job_import", cached: false } }); });
  await page.route("**/api/content/pkg_new", (r) => r.fulfill({ json: importStatus === "failed"
    ? { status: "failed", job_id: "job_import", error: { code: "import_failed", message: "файл повреждён" } }
    : { status: "succeeded", job_id: "job_import", package: { package_id: "pkg_new", blocks: [], facts: [], datasets: [], assets: [] } } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/templates/tpl_quiet", (r) => r.fulfill({ json: { status: "succeeded", job_id: "job_tpl", name: "Шаблон", previews: [], profile: { assets: [] } } }));
  await page.route(/\/thumbnail$/, (r) => r.fulfill({ contentType: "image/png", body: PNG }));
  await page.route("**/api/generations/job_quiet", (r) => r.fulfill({ json: {
    job_id: "job_quiet", status: "succeeded", stage: "done", metrics: { totals: { duration_ms: 1000 } }, execution_mode: { mode: "real", layers: {} },
    variants: [{ variant_id: "compact", status: "ready", revision: 1, artifacts: {} }],
  } }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: false } }));
  return state;
}

test("картинка из «Файлов» при готовой презентации — лента молчит, старая запись о загрузке скрыта", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/project?id=quiet-test");
  const chat = page.getByTestId("chat-list");
  await expect(page.getByTestId("chat-greeting")).toBeVisible();
  await expect(chat.getByTestId("msg-user")).toHaveCount(0);
  const before = await chat.getByTestId("msg-assistant").count();

  await page.getByTestId("tab-files").click();
  const [chooser] = await Promise.all([page.waitForEvent("filechooser"), page.getByTestId("files-add").click()]);
  await chooser.setFiles([{ name: "фото.png", mimeType: "image/png", buffer: PNG }]);
  await expect(page.getByTestId("file-file_new")).toBeVisible();
  await expect.poll(() => state.imports).toBe(1);

  await page.getByTestId("tab-chat").click();
  await page.waitForTimeout(2500);
  await expect(chat.getByTestId("msg-user")).toHaveCount(0);
  await expect(chat.getByTestId("content-card")).toHaveCount(0);
  await expect(chat.getByTestId("brief-card")).toHaveCount(0);
  expect(await chat.getByTestId("msg-assistant").count()).toBe(before);
});

test("вложение без текста из чата тоже молча уходит в файлы, упавший разбор — сообщение", async ({ page }) => {
  await setup(page, { importStatus: "failed" });
  await page.goto("/project?id=quiet-test");
  const chat = page.getByTestId("chat-list");
  const chooser = page.waitForEvent("filechooser");
  await page.getByTestId("chat-attach").click();
  await (await chooser).setFiles([{ name: "фото.png", mimeType: "image/png", buffer: PNG }]);
  await page.getByTestId("chat-send").click();
  await expect(chat.getByText(/Не смог прочитать материалы: файл повреждён/)).toBeVisible({ timeout: 10000 });
  await expect(chat.getByTestId("msg-user")).toHaveCount(0);
  await expect(chat.getByTestId("content-card")).toHaveCount(0);
});

test("PPTX: вопрос называет файл, пузыря с файлом нет", async ({ page }) => {
  await setup(page);
  await page.route("**/api/projects/quiet-test/files", async (r) => {
    await new Promise((resolve) => setTimeout(resolve, 800));
    return r.fulfill({ status: 201, json: [file("file_deck", "Отчёт.pptx", "pptx", "other")] });
  });
  await page.goto("/project?id=quiet-test");
  const chat = page.getByTestId("chat-list");
  const chooser = page.waitForEvent("filechooser");
  await page.getByTestId("chat-attach").click();
  await (await chooser).setFiles({ name: "Отчёт.pptx", mimeType: PPTX_MIME, buffer: Buffer.from("deck") });
  // Пока файл едет и после загрузки: только вопрос.
  await expect(chat.locator('[data-testid^="template-question-"]').last()).toContainText("«Отчёт.pptx» похоже на презентацию");
  await expect(chat.getByTestId("msg-user")).toHaveCount(0);
  await page.waitForTimeout(1200);
  await expect(chat.locator('[data-testid^="template-question-"]')).toHaveCount(1);
  await expect(chat.getByTestId("msg-user")).toHaveCount(0);
});

test("PPTX шаблоном: «разберу, как только загрузится» → «загружен, приступаю» → ход с таймером → итог со временем", async ({ page }) => {
  const project: Record<string, unknown> = {
    schema_version: "1.5", project_id: "tpl-flow", title: "Шаблон из чата", template_id: null, package_id: null, job_id: null, chosen_variant: null,
    created_at: "2026-09-24T10:00:00Z", updated_at: "2026-09-24T10:00:00Z", brief: { purpose: "", title: "", audience: "", goal: "", language: "ru", tone: "", must_include: [], avoid: [] },
    settings: { mode: "range", min: 10, max: 15, exact: 12, variants: ["compact"], contextual: true, images: false, seed: null, force: false },
    files: [], events: [],
  };
  const deck = file("file_tpl", "Шаблон VK.pptx", "pptx", "other");
  let analyzed = false;
  await page.route("**/api/projects/tpl-flow", async (r) => {
    if (r.request().method() === "PATCH") Object.assign(project, r.request().postDataJSON());
    return r.fulfill({ json: project });
  });
  await page.route("**/api/projects/tpl-flow/events", (r) => r.fulfill({ json: { ...r.request().postDataJSON(), event_id: `evt_${Date.now()}${Math.random()}`, at: new Date().toISOString() } }));
  await page.route("**/api/projects/tpl-flow/files", async (r) => {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    return r.fulfill({ status: 201, json: [deck] });
  });
  await page.route("**/api/projects/tpl-flow/files/*", (r) => r.fulfill({ json: { ...deck, kind: "template", template_id: "tpl_new" } }));
  await page.route("**/api/templates", (r) => r.request().method() === "POST"
    ? r.fulfill({ status: 202, json: { template_id: "tpl_new", job_id: "job_tpl", cached: false } })
    : r.fulfill({ json: [] }));
  await page.route("**/api/templates/tpl_new", (r) => r.fulfill({ json: analyzed
    ? { status: "succeeded", job_id: "job_tpl", name: "Шаблон VK.pptx", previews: [], timing: { started_at: "2026-09-24T10:00:00Z", finished_at: "2026-09-24T10:00:29Z", duration_ms: 29000 },
      profile: { patterns: [{}, {}, {}], stats: { slides: 55 }, assets: [], design_tokens: { typography: { fonts: [{ family: "Play" }, { family: "Arial" }] }, colors: { palette: [{ hex: "#0077FF", role: "accent" }] } } } }
    : { status: "running", job_id: "job_tpl", name: "Шаблон VK.pptx", previews: [], timing: { created_at: new Date(Date.now() - 14000).toISOString(), started_at: new Date(Date.now() - 14000).toISOString() } } }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: false } }));

  // Перехват Playwright не присылает ход отправки: половина файла «уходит» сразу после send.
  await page.addInitScript(() => {
    const send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function (body) {
      send.call(this, body);
      setTimeout(() => this.upload.dispatchEvent(new ProgressEvent("progress", { lengthComputable: true, loaded: 2, total: 4 })), 50);
    };
  });
  await page.goto("/project?id=tpl-flow");
  const chat = page.getByTestId("chat-list");
  const chooser = page.waitForEvent("filechooser");
  await page.getByTestId("chat-attach").click();
  await (await chooser).setFiles({ name: "Шаблон VK.pptx", mimeType: PPTX_MIME, buffer: Buffer.from("deck") });
  await page.getByTestId("answer-template").click();
  await expect(chat).toContainText("Разберу «Шаблон VK.pptx» как шаблон, как только файл загрузится.");
  // Пока файл едет — строка загрузки с процентом под этой же репликой.
  const upload = chat.getByTestId("upload-progress");
  await expect(upload).toContainText("Загружаю файл · 4 Б");
  await expect(chat.getByTestId("upload-progress-percent")).toHaveText("50%");

  await expect(chat).toContainText("«Шаблон VK.pptx» загружен, приступаю к разбору шаблона.");
  await expect(upload).toHaveCount(0);
  const progress = chat.getByTestId("template-progress");
  await expect(progress).toBeVisible();
  await expect(chat.getByTestId("template-progress-timer")).toHaveText(/^0:1\d$/);
  // Второй фразы «Разбираю…» нет: ход разбора — строка с таймером.
  await expect(chat.getByText(/Разбираю/)).toHaveCount(0);
  await expect(chat.getByTestId("msg-user")).toHaveCount(0);

  analyzed = true;
  await expect(chat.getByTestId("template-profile")).toContainText("Разобрал шаблон «Шаблон VK.pptx»: 3 композиции на 55 слайдах, шрифты Play, Arial.", { timeout: 10000 });
  await expect(chat.getByTestId("template-duration")).toHaveText("Разобран за 29 с");
  await expect(progress).toHaveCount(0);
});
