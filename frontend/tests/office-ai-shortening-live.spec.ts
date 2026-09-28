import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";

const digest = (data: Buffer) => createHash("sha256").update(data).digest("hex");

// Real UI/API/OpenLux/SDK on a fresh sandbox COPY. Do not retain signed URLs.
test.use({ trace: "off" });
test("Education shortening survives save, fresh-page reopen and UI download", async ({ page, request }, testInfo) => {
  const projectId = process.env.OFFICE_AI_PROJECT_ID;
  const id = process.env.OFFICE_AI_DOCUMENT_ID;
  test.skip(!projectId || !id || !process.env.OFFICE_AI_SHA256,
    "Requires a fresh seed_office_sdk.py --source --project copy and a live OpenLux stack");
  test.setTimeout(600_000);
  expect(id).toMatch(/^[a-f0-9]{32}$/);
  expect(projectId).toMatch(/^prj_[a-f0-9]+$/);
  const slide = Number(process.env.OFFICE_AI_SLIDE ?? 2);
  const slideCount = Number(process.env.OFFICE_AI_SLIDES ?? 10);
  const shapeId = process.env.OFFICE_AI_SHAPE_ID ?? "268";
  expect(Number.isInteger(slide)).toBe(true);
  expect(Number.isInteger(slideCount)).toBe(true);
  expect(slide).toBeGreaterThan(0);
  expect(slide).toBeLessThanOrEqual(slideCount);
  expect(shapeId).toMatch(/^\d+$/);
  const target = { slide, shape_id: shapeId };
  const instruction = process.env.OFFICE_AI_INSTRUCTION
    ?? "Сократи текст выбранного объекта, сохрани смысл, все числа, единицы и периоды. Не меняй оформление.";
  // Optionally serve a freshly built frontend without replacing the running stack.
  // Keep the same browser origin for the real SDK save adapter. API, ONLYOFFICE
  // (including websocket traffic) and downloads always use the live deployment.
  const staticURL = process.env.OFFICE_AI_STATIC_URL;
  if (staticURL) {
    const origin = new URL(testInfo.project.use.baseURL!).origin;
    await page.context().route(`${origin}/**`, async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/onlyoffice/")) {
        await route.continue();
        return;
      }
      const response = await route.fetch({ url: new URL(url.pathname + url.search, staticURL).href });
      await route.fulfill({ response });
    });
  }
  const healthResponse = await request.get("/api/health");
  expect(healthResponse.ok()).toBeTruthy();
  const health = await healthResponse.json();
  expect(health.status).toBe("ok");
  expect(health.provider.host).toBe("https://api.openlux.ai");
  expect(health.provider.roles.llm.model).toBe("qwen3.8-27b");
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
  const initial = await metadata();
  expect(initial.source).toMatch(/^job_sdk_sandbox_[a-f0-9]{32}\/compact\/r1\/deck.pptx$/);
  expect(initial.revision).toBe(0);
  expect(initial.active_key).toBeNull();
  const originalHash = await download(0, "before.pptx");
  expect(originalHash).toBe(process.env.OFFICE_AI_SHA256);
  await page.goto(`/project?id=${projectId}`);
  await expect(page.getByText("Превью · v0", { exact: true })).toBeVisible({ timeout: 120_000 });
  await page.getByTestId(`thumb-${slide - 1}`).click();
  await expect(page.getByTestId("slide-counter")).toHaveText(`Слайд ${slide} из ${slideCount}`);
  const outline = page.getByTestId(`object-outline-${shapeId}`);
  await expect(outline).toBeVisible();
  await outline.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("office-object-target")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("selected.png"), fullPage: true });
  await page.getByTestId("chat-input").fill(instruction);
  const edit = page.waitForResponse(r => r.url().endsWith(`${base}/edit`) && r.request().method() === "POST", { timeout: 240_000 });
  await page.getByTestId("chat-send").click();
  const response = await edit;
  expect(response.request().postDataJSON()).toEqual({ revision: 0, instruction, target });
  expect(response.ok()).toBeTruthy();
  const result = await response.json();
  await writeFile(testInfo.outputPath("edit.json"), JSON.stringify(result, null, 2));
  expect(result.changed).toBe(true); // An empty safe refusal is NOT acceptance.
  const revision = result.document.revision;
  expect(revision).toBe(1);
  await expect(page.getByText(`Превью · v${revision}`, { exact: true })).toBeVisible({ timeout: 120_000 });
  const canvas = page.locator(".preview-stage img");
  await expect(canvas).toHaveAttribute("src", `${base}/preview/${revision}/slide-${String(slide).padStart(2, "0")}.png`);
  await expect.poll(() => canvas.evaluate((node: HTMLImageElement) => node.complete && node.naturalWidth > 0)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("shortened.png"), fullPage: true });
  const savedHash = await download(revision, "after.pptx");
  expect(savedHash).not.toBe(originalHash);

  // Open the saved AI revision in the real editor, finish/save without manual edits.
  await page.getByTestId("open-office").click();
  await expect(page.frameLocator("iframe").locator("#id-toolbar-btn-add-slide")).toBeVisible({ timeout: 180_000 });
  await expect(page.getByTestId("office-loading")).toHaveCount(0, { timeout: 180_000 });
  await page.getByRole("button", { name: "Завершить и сохранить", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/project\\?id=${projectId}`), { timeout: 120_000 });
  await expect(page.getByText(`Превью · v${revision}`, { exact: true })).toBeVisible({ timeout: 120_000 });
  const saved = await metadata();
  expect(saved.active_key).toBeNull();
  expect(saved.error).toBeNull();
  expect(await download(saved.revision, "saved.pptx")).toBe(savedHash);

  // Close the page, then reopen the project and download through its UI.
  await page.close();
  const reopened = await page.context().newPage();
  await reopened.goto(`/project?id=${projectId}`);
  await expect(reopened.getByText(`Превью · v${revision}`, { exact: true })).toBeVisible({ timeout: 120_000 });
  await reopened.getByTestId(`thumb-${slide - 1}`).click();
  await expect(reopened.getByTestId("slide-counter")).toHaveText(`Слайд ${slide} из ${slideCount}`);
  await reopened.getByTestId("download-menu").click();
  const downloaded = reopened.waitForEvent("download");
  await reopened.getByTestId("dl-pptx").click();
  await (await downloaded).saveAs(testInfo.outputPath("ui-downloaded.pptx"));
  expect(digest(await readFile(testInfo.outputPath("ui-downloaded.pptx")))).toBe(savedHash);
  expect(await download((await metadata()).revision, "reopened.pptx")).toBe(savedHash);
  expect(await download(0, "original-after.pptx")).toBe(originalHash);
  await reopened.screenshot({ path: testInfo.outputPath("reopened.png"), fullPage: true });
  await writeFile(testInfo.outputPath("report.json"), JSON.stringify({
    project_id: projectId, document_id: id, original_sha256: originalHash,
    saved_sha256: savedHash, revision, active_key: saved.active_key,
    error: saved.error, original_unchanged: true, editor_save_unchanged: true,
    proposal_confirmation: false, reopen_mode: "fresh-page",
    scope: "Live selected-object AI edit, SDK no-op save, project reopen and UI download; separate PPTX audit required",
    frontend: staticURL ? "isolated-current-build" : "deployed",
    target, instruction, slides: slideCount,
  }, null, 2));
});