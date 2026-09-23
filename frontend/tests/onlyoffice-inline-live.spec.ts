import { expect, test } from "@playwright/test";
import { writeFile } from "node:fs/promises";

// Isolated seed only. No SDK interception; do not retain signed config URLs in traces.
test.use({ trace: "off" });
test("real compact editor opens in Slides, saves a slide and returns to editable PPTX", async ({ page, request }) => {
  test.skip(!process.env.ONLYOFFICE_INLINE_PROJECT_ID || !process.env.ONLYOFFICE_INLINE_DOCUMENT_ID,
    "Requires a fresh seed_office_sdk.py --project sandbox and live stack");
  test.setTimeout(240_000);
  const projectId = process.env.ONLYOFFICE_INLINE_PROJECT_ID!;
  const documentId = process.env.ONLYOFFICE_INLINE_DOCUMENT_ID!;
  const base = `/api/office/documents/${documentId}`;
  const initial = await (await request.get(base)).json();
  expect(initial.source).toMatch(/^job_sdk_sandbox_[a-f0-9]{32}\/compact\/r1\/deck.pptx$/);
  expect(initial.revision).toBe(0);
  expect(initial.active_key).toBeNull();
  expect(initial.error).toBeNull();
  const project = await (await request.get(`/api/projects/${projectId}`)).json();
  expect(project.title).toMatch(/^SDK sandbox [a-f0-9]{32}$/);
  expect(initial.source).toBe(`${project.job_id}/compact/r1/deck.pptx`);
  const original = await request.get(`${base}/download/0`);
  expect(original.ok()).toBeTruthy();
  const originalBytes = await original.body();
  const configResponse = page.waitForResponse((response) => response.url().endsWith(`${base}/config`) && response.request().method() === "POST");
  await page.goto(`/project?id=${projectId}`);
  const config = (await (await configResponse).json()).config;
  expect(config.editorConfig.customization).toMatchObject({
    uiTheme: "theme-white", compactToolbar: true, hideNotes: true, hideRightMenu: true,
    hideRulers: true, autosave: true, forcesave: true,
  });
  const frame = page.getByTestId("preview-pane").locator("iframe");
  await expect(frame).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("office-preview")).toBeEnabled({ timeout: 120_000 });
  await expect(page).toHaveURL(new RegExp(`/project\\?id=${projectId}$`));
  await page.screenshot({ path: test.info().outputPath("compact-editor.png"), fullPage: true });
  const editor = page.frameLocator("iframe");
  await expect(editor.locator("#status-label-pages")).toHaveText("Слайд 1 из 1");
  // Compact toolbar expands on tab click; it is not permanently removed.
  await editor.getByText("Главная", { exact: true }).click();
  await expect(editor.locator("#id-toolbar-btn-add-slide")).toBeVisible();
  await editor.locator("#id-toolbar-btn-add-slide").click();
  await expect(editor.locator("#status-label-pages")).toHaveText("Слайд 2 из 2");
  await page.getByTestId("office-preview").click();
  await expect(page.getByTestId("slide-counter")).toHaveText("Слайд 1 из 2", { timeout: 120_000 });
  await expect(frame).toHaveCount(0);
  const saved = await (await request.get(base)).json();
  expect(saved.active_key).toBeNull();
  expect(saved.error).toBeNull();
  expect(saved.revision).toBeGreaterThan(0);
  const download = await request.get(`${base}/download/${saved.revision}`);
  expect(download.ok()).toBeTruthy();
  const savedBytes = await download.body();
  expect(savedBytes.subarray(0, 2).toString()).toBe("PK");
  expect(savedBytes.equals(originalBytes)).toBe(false);
  await writeFile(test.info().outputPath("saved.pptx"), savedBytes);
  await page.getByTestId("edit-slides").click();
  await expect(frame).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("office-preview")).toBeEnabled({ timeout: 120_000 });
  await expect(editor.locator("#status-label-pages")).toHaveText(/Слайд \d из 2/);
  // A second real edit proves the saved revision reopened editably, not just visibly.
  await editor.getByText("Главная", { exact: true }).click();
  await editor.locator("#id-toolbar-btn-add-slide").click();
  await expect(editor.locator("#status-label-pages")).toHaveText(/Слайд \d из 3/);
  await page.getByTestId("office-preview").click();
  await expect(page.getByTestId("slide-counter")).toHaveText("Слайд 1 из 3", { timeout: 120_000 });
  await expect(frame).toHaveCount(0);
  const final = await (await request.get(base)).json();
  expect(final.active_key).toBeNull();
  expect(final.error).toBeNull();
  expect(final.revision).toBeGreaterThan(saved.revision);
  const unchanged = await request.get(`${base}/download/0`);
  expect(unchanged.ok()).toBeTruthy();
  expect((await unchanged.body()).equals(originalBytes)).toBe(true);
  await page.screenshot({ path: test.info().outputPath("saved-preview.png"), fullPage: true });
});