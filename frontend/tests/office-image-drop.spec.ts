import { expect, test, type Locator, type Page } from "@playwright/test";

// Картинка из панели «Файлы» перетаскивается на слайд открытого редактора: сервер подписывает
// команду, редактор получает её в docEditor.insertImage. SDK и API — заглушки теста.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC", "base64");

const file = (file_id: string, name: string, format: string) => ({
  schema_version: "1.2", file_id, name, size_bytes: 2048, sha256: file_id.padEnd(64, "0"), mime: "application/octet-stream",
  kind: "other", added_at: "2026-09-24T10:00:00Z", check: { status: "ok", format },
});

async function setup(page: Page) {
  const state = { failCommand: false };
  await page.route("**/api/projects/drop-test", (r) => r.fulfill({ json: {
    schema_version: "1.5", project_id: "drop-test", title: "Перетаскивание", template_id: "tpl_drop", package_id: null, job_id: "job_droptest", chosen_variant: null,
    created_at: "2026-09-24T10:00:00Z", updated_at: "2026-09-24T10:00:00Z", brief: {}, settings: {}, events: [],
    files: [file("file_photo", "фото.png", "image"), file("file_notes", "заметки.docx", "docx")],
  } }));
  await page.route("**/api/projects/drop-test/events", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/templates/tpl_drop", (r) => r.fulfill({ json: {
    status: "succeeded", job_id: "job_tpl", name: "Шаблон", previews: [],
    profile: { assets: [{ asset_id: "a_logo", kind: "logo", media_path: "ppt/media/image2.png", sha256: "sha-logo", tags: ["логотип"] }] },
  } }));
  await page.route(/\/(thumbnail|media\/a_logo)$/, (r) => r.fulfill({ contentType: "image/png", body: PNG }));
  await page.route("**/api/generations/job_droptest", (r) => r.fulfill({ json: {
    job_id: "job_droptest", status: "succeeded", stage: "done", metrics: { totals: { duration_ms: 1000 } }, execution_mode: { mode: "real", layers: {} },
    variants: [{ variant_id: "balanced", status: "ready", revision: 1, artifacts: { pptx: "balanced/r1/deck.pptx" } }],
  } }));
  const doc = { id: "drop-doc", source: "job_droptest/balanced/r1/deck.pptx", title: "Test", revision: 1, active_key: "key", error: null, revisions: [{ revision: 1, sha256: "x", saved_at: 1 }] };
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: true } }));
  await page.route("**/api/office/documents", (r) => r.fulfill({ json: doc }));
  await page.route("**/api/office/documents/drop-doc", (r) => r.fulfill({ json: doc }));
  await page.route("**/api/office/documents/drop-doc/config", (r) => r.fulfill({ json: { script_url: "/office-drop-sdk.js", config: {} } }));
  await page.route("**/api/office/documents/drop-doc/images", (r) => {
    if (state.failCommand) return r.fulfill({ status: 422, json: { error: { code: "file_not_image", message: "На слайд вставляются картинки PNG или JPEG" } } });
    const body = r.request().postDataJSON() as Record<string, string>;
    const url = body.file_id ? `http://api:8000/api/projects/${body.project_id}/files/${body.file_id}/content` : `http://api:8000/api/templates/${body.template_id}/media/${body.asset_id}`;
    return r.fulfill({ json: { c: "add", images: [{ fileType: "png", url }], token: "signed" } });
  });
  await page.route("**/office-drop-sdk.js", (r) => r.fulfill({ contentType: "application/javascript", body: `
    window.testInserted = [];
    window.DocsAPI = { DocEditor: function(id, config) {
      const frame = document.createElement('iframe'); document.getElementById(id).appendChild(frame);
      setTimeout(() => config.events.onDocumentReady(), 0);
      this.requestClose = () => config.events.onRequestClose();
      this.destroyEditor = () => frame.remove();
      this.insertImage = (command) => window.testInserted.push(command);
    }};
  ` }));
  return state;
}

/** HTML5-перетаскивание по шагам: слой-приёмник появляется только после начала перетаскивания. */
async function dragToEditor(page: Page, source: Locator) {
  await source.hover();
  await page.mouse.down();
  const canvas = await page.locator(".office-canvas").boundingBox();
  await page.mouse.move(canvas!.x + canvas!.width / 2, canvas!.y + canvas!.height / 2, { steps: 8 });
  await expect(page.getByTestId("office-drop")).toBeVisible();
  await page.mouse.move(canvas!.x + canvas!.width / 2 + 10, canvas!.y + canvas!.height / 2 + 10, { steps: 2 });
  await page.mouse.up();
}

test("файл проекта и ресурс шаблона встают на слайд подписанной командой", async ({ page }) => {
  await setup(page);
  await page.goto("/project?id=drop-test");
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
  await page.getByTestId("tab-files").click();
  // Картинку тащить можно, документ — нет.
  await expect(page.getByTestId("file-file_photo")).toHaveAttribute("draggable", "true");
  await expect(page.getByTestId("file-file_notes")).not.toHaveAttribute("draggable", "true");
  await expect(page.getByTestId("office-drop")).toHaveCount(0);

  await dragToEditor(page, page.getByTestId("file-file_photo"));
  await expect.poll(() => page.evaluate("window.testInserted.length")).toBe(1);
  expect(await page.evaluate("window.testInserted[0]")).toEqual({ c: "add", images: [{ fileType: "png", url: "http://api:8000/api/projects/drop-test/files/file_photo/content" }], token: "signed" });
  await expect(page.getByTestId("office-drop")).toHaveCount(0);

  await page.getByTestId("files-tab-template").click();
  await dragToEditor(page, page.getByTestId("template-asset-a_logo"));
  await expect.poll(() => page.evaluate("window.testInserted.length")).toBe(2);
  expect(await page.evaluate("window.testInserted[1].images[0].url")).toBe("http://api:8000/api/templates/tpl_drop/media/a_logo");
});

test("отказ сервера — уведомление, редактор не трогается", async ({ page }) => {
  const state = await setup(page);
  state.failCommand = true;
  await page.goto("/project?id=drop-test");
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
  await page.getByTestId("tab-files").click();
  await dragToEditor(page, page.getByTestId("file-file_photo"));
  await expect(page.getByText("Картинка не вставлена")).toBeVisible();
  await expect(page.getByText("На слайд вставляются картинки PNG или JPEG")).toBeVisible();
  expect(await page.evaluate("window.testInserted.length")).toBe(0);
});
