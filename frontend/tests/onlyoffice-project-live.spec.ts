import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";

const digest = (data: Buffer) => createHash("sha256").update(data).digest("hex");

// Real project/API/SDK, no route interception. Never retain signed source/callback URLs.
test.use({ trace: "off" });
test("synthetic project returns from real fullscreen save to the inline editor on the saved revision", async ({ page, request }, testInfo) => {
  test.skip(!process.env.ONLYOFFICE_SDK_PROJECT_ID || !process.env.ONLYOFFICE_SDK_DOCUMENT_ID || !process.env.ONLYOFFICE_SDK_SHA256,
    "Requires a fresh scripts/seed_office_sdk.py --project seed and a real local stack");
  test.setTimeout(420_000);
  const projectId = process.env.ONLYOFFICE_SDK_PROJECT_ID!;
  const id = process.env.ONLYOFFICE_SDK_DOCUMENT_ID!;
  expect(id).toMatch(/^[a-f0-9]{32}$/);
  expect(projectId).toMatch(/^prj_[a-f0-9]+$/);
  const base = `/api/office/documents/${id}`;
  const initialResponse = await request.get(base);
  expect(initialResponse.ok()).toBeTruthy();
  const initial = await initialResponse.json();
  expect(initial.source).toMatch(/^job_sdk_sandbox_[a-f0-9]{32}\/compact\/r1\/deck.pptx$/);
  expect(initial.revision).toBe(0);
  expect(initial.active_key).toBeNull();
  expect(initial.error).toBeNull();
  expect(initial.revisions).toHaveLength(1);
  const projectResponse = await request.get(`/api/projects/${projectId}`);
  expect(projectResponse.ok()).toBeTruthy();
  const project = await projectResponse.json();
  expect(initial.source).toBe(`${project.job_id}/compact/r1/deck.pptx`);
  expect(project.title).toMatch(/^SDK sandbox [a-f0-9]{32}$/);
  const originalResponse = await request.get(`${base}/download/0`);
  expect(originalResponse.ok()).toBeTruthy();
  const original = await originalResponse.body();
  expect(digest(original)).toBe(process.env.ONLYOFFICE_SDK_SHA256);

  const inline = page.getByTestId("preview-pane").locator("iframe");
  await page.goto(`/project?id=${projectId}`);
  await expect(inline).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("office-loading")).toHaveCount(0, { timeout: 180_000 });
  await expect(page.frameLocator("iframe").locator("#status-label-pages")).toHaveText("Слайд 1 из 1");

  // A marker in the JS realm catches accidental full-page navigation/reload.
  await page.evaluate(() => Object.defineProperty(window, "sdkProjectNavigation", { value: true }));
  await page.goto(`/office?${new URLSearchParams({ id, project: projectId, officeJob: project.job_id, officeArtifact: "compact/r1/deck.pptx" })}`);
  await expect(page).toHaveURL(new RegExp(`/office\\?id=${id}&project=${projectId}&`));
  await expect(page.locator("iframe")).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("office-loading")).toHaveCount(0, { timeout: 180_000 });
  await expect(page.getByText("Ошибка ONLYOFFICE.", { exact: false })).toHaveCount(0);
  const editor = page.frameLocator("iframe");
  await editor.getByText("Главная", { exact: true }).click();
  await editor.locator("#id-toolbar-btn-add-slide").click();
  await expect(editor.locator("#status-label-pages")).toHaveText("Слайд 2 из 2");
  await page.getByRole("button", { name: "Завершить и сохранить", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/project\\?id=${projectId}&officeJob=`), { timeout: 120_000 });
  expect(await page.evaluate(() => Object.hasOwn(window, "sdkProjectNavigation"))).toBe(true);
  const savedResponse = await request.get(base);
  expect(savedResponse.ok()).toBeTruthy();
  const saved = await savedResponse.json();
  expect(saved.revision).toBeGreaterThan(0);
  expect(saved.active_key).toBeNull();
  expect(saved.error).toBeNull();
  // Проект снова открывает встроенный редактор — уже на сохранённой ревизии с двумя слайдами.
  await expect(inline).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("office-loading")).toHaveCount(0, { timeout: 180_000 });
  await expect(page.frameLocator("iframe").locator("#status-label-pages")).toHaveText(/Слайд \d из 2/, { timeout: 120_000 });
  await page.screenshot({ path: testInfo.outputPath("project-saved.png"), fullPage: true });
  const download = await request.get(`${base}/download/${saved.revision}`);
  expect(download.ok()).toBeTruthy();
  const updated = await download.body();
  expect(digest(updated)).not.toBe(digest(original));
  await writeFile(testInfo.outputPath("saved.pptx"), updated);
  const unchanged = await request.get(`${base}/download/0`);
  expect(unchanged.ok()).toBeTruthy();
  expect(digest(await unchanged.body())).toBe(digest(original));
  await writeFile(testInfo.outputPath("report.json"), JSON.stringify({
    project_id: projectId, document_id: id, source: initial.source,
    saved_revision: saved.revision, active_key: saved.active_key, error: saved.error,
    original_sha256: digest(original), saved_sha256: digest(updated),
    original_unchanged: true,
    slides_before: 1, slides_after: 2, returned_without_reload: true,
    scope: "real synthetic project/API/SDK fullscreen save and inline reopen on the saved revision; not brand or AI",
  }, null, 2));
});
