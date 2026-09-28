import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";

const digest = (data: Buffer) => createHash("sha256").update(data).digest("hex");

// Opt-in sandbox copies only. Do not record signed ONLYOFFICE URLs in traces.
test.use({ trace: "off" });
test("accepted PPTX survives editor save, fresh-page reopen and UI download", async ({ page, request }, testInfo) => {
  test.skip(!process.env.OFFICE_ARTIFACT_SEED,
    "Requires a fresh seed_office_sdk.py --source --project sandbox and a deployed stack");
  test.setTimeout(600_000);
  const seed = JSON.parse(await readFile(process.env.OFFICE_ARTIFACT_SEED!, "utf8"));
  const { project_id: projectId, document_id: id, sha256, slides } = seed;
  expect(id).toMatch(/^[a-f0-9]{32}$/);
  expect(projectId).toMatch(/^prj_[a-f0-9]+$/);
  expect(sha256).toMatch(/^[a-f0-9]{64}$/);
  const base = `/api/office/documents/${id}`;
  const metadata = async () => {
    const response = await request.get(base);
    expect(response.ok()).toBeTruthy();
    return response.json();
  };
  const download = async (revision: number, filename: string) => {
    const response = await request.get(`${base}/download/${revision}`);
    expect(response.ok()).toBeTruthy();
    const bytes = await response.body();
    await writeFile(testInfo.outputPath(filename), bytes);
    return digest(bytes);
  };
  const health = await request.get("/api/health");
  expect(health.ok()).toBeTruthy();
  expect((await health.json()).status).toBe("ok");
  const initial = await metadata();
  expect(initial.source).toMatch(/^job_sdk_sandbox_[a-f0-9]{32}\/compact\/r1\/deck.pptx$/);
  expect(initial.revision).toBe(0);
  expect(initial.active_key).toBeNull();
  expect(initial.error).toBeNull();
  expect(await download(0, "before.pptx")).toBe(sha256);
  const inline = page.getByTestId("preview-pane").locator("iframe");
  await page.goto(`/project?id=${projectId}`);
  await expect(inline).toBeVisible({ timeout: 180_000 });
  await expect(page.getByTestId("office-loading")).toHaveCount(0, { timeout: 180_000 });
  await expect(page.frameLocator("iframe").locator("#status-label-pages")).toHaveText(`Слайд 1 из ${slides}`);
  await page.getByTestId("open-office").click();
  await expect(page.frameLocator("iframe").locator("#id-toolbar-btn-add-slide")).toBeVisible({ timeout: 180_000 });
  await expect(page.getByTestId("office-loading")).toHaveCount(0, { timeout: 180_000 });
  await page.getByRole("button", { name: "Завершить и сохранить", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/project\\?id=${projectId}`), { timeout: 120_000 });
  await expect(inline).toBeVisible({ timeout: 180_000 });
  await expect(page.getByTestId("office-loading")).toHaveCount(0, { timeout: 180_000 });
  const saved = await metadata();
  expect(saved.active_key).toBeNull();
  expect(saved.error).toBeNull();
  expect(saved.revision).toBe(0);
  expect(await download(saved.revision, "saved.pptx")).toBe(sha256);
  await page.close();
  const reopened = await page.context().newPage();
  await reopened.goto(`/project?id=${projectId}`);
  await expect(reopened.getByTestId("preview-pane").locator("iframe")).toBeVisible({ timeout: 180_000 });
  await expect(reopened.getByTestId("office-loading")).toHaveCount(0, { timeout: 180_000 });
  await expect(reopened.frameLocator("iframe").locator("#status-label-pages")).toHaveText(`Слайд 1 из ${slides}`);
  await reopened.getByTestId("download-menu").click();
  const downloaded = reopened.waitForEvent("download");
  await reopened.getByTestId("dl-pptx").click();
  await (await downloaded).saveAs(testInfo.outputPath("ui-downloaded.pptx"));
  expect(digest(await readFile(testInfo.outputPath("ui-downloaded.pptx")))).toBe(sha256);
  expect(await download(0, "original-after.pptx")).toBe(sha256);
  await reopened.screenshot({ path: testInfo.outputPath("reopened.png"), fullPage: true });
  await writeFile(testInfo.outputPath("report.json"), JSON.stringify({
    project_id: projectId, document_id: id, sha256, slides,
    revision: saved.revision, active_key: saved.active_key, error: saved.error,
    original_unchanged: true, editor_save_unchanged: true, ui_download_unchanged: true,
    frontend: "deployed", reopen_mode: "fresh-page-inline-editor",
    scope: "Previously accepted PPTX; SDK no-op save, project reopen and UI download. No new model call or manual edit.",
  }, null, 2));
});