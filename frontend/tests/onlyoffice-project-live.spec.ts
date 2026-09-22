import { expect, test, type Locator } from "@playwright/test";
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";

const digest = (data: Buffer) => createHash("sha256").update(data).digest("hex");
async function decoded(image: Locator) {
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.complete && node.naturalWidth > 0)).toBe(true);
}

// Real project/API/SDK, no route interception. Never retain signed source/callback URLs.
test.use({ trace: "off" });
test("synthetic project returns from real SDK save to current canvas and thumbnails", async ({ page, request }, testInfo) => {
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

  await page.goto(`/project?id=${projectId}`);
  await expect(page.getByText("Превью · v0", { exact: true })).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("slide-counter")).toHaveText("Слайд 1 из 1");
  const canvas = page.locator(".preview-stage img");
  await decoded(canvas);
  const beforeSrc = await canvas.getAttribute("src");
  expect(beforeSrc).toBe(`${base}/preview/0/slide-01.png`);
  await decoded(page.getByTestId("thumb-0").locator("img"));
  await expect(page.locator("iframe")).toHaveCount(0);

  // A marker in the JS realm catches accidental full-page navigation/reload.
  await page.evaluate(() => Object.defineProperty(window, "sdkProjectNavigation", { value: true }));
  await page.getByTestId("open-office").click();
  await expect(page).toHaveURL(new RegExp(`/office\\?id=${id}&project=${projectId}&`));
  await expect(page.locator("iframe")).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText("Загружается редактор…")).toHaveCount(0, { timeout: 180_000 });
  await expect(page.getByText("Ошибка ONLYOFFICE.", { exact: false })).toHaveCount(0);
  await page.frameLocator("iframe").locator("#id-toolbar-btn-add-slide").click();
  await page.getByRole("button", { name: "Завершить и сохранить", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/project\\?id=${projectId}&officeJob=`), { timeout: 120_000 });
  expect(await page.evaluate(() => Object.hasOwn(window, "sdkProjectNavigation"))).toBe(true);
  await expect(page.locator("iframe")).toHaveCount(0);
  const savedResponse = await request.get(base);
  expect(savedResponse.ok()).toBeTruthy();
  const saved = await savedResponse.json();
  expect(saved.revision).toBeGreaterThan(0);
  expect(saved.active_key).toBeNull();
  expect(saved.error).toBeNull();
  await expect(page.getByText(`Превью · v${saved.revision}`, { exact: true })).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("slide-counter")).toHaveText("Слайд 1 из 2");
  await expect(canvas).toHaveAttribute("src", `${base}/preview/${saved.revision}/slide-01.png`);
  await decoded(canvas);
  const thumbnails = page.locator(".thumb-image img");
  await expect(thumbnails).toHaveCount(2);
  for (let index = 0; index < 2; index++) {
    const expected = `${base}/preview/${saved.revision}/slide-0${index + 1}.png`;
    await expect(thumbnails.nth(index)).toHaveAttribute("src", expected);
    await decoded(thumbnails.nth(index));
    await page.getByTestId(`thumb-${index}`).click();
    await expect(canvas).toHaveAttribute("src", expected);
    await decoded(canvas);
  }
  await expect(page.getByTestId("slide-counter")).toHaveText("Слайд 2 из 2");
  await expect(page.locator(`img[src*="${base}/preview/0/"]`)).toHaveCount(0);
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
    original_unchanged: true, before_src: beforeSrc,
    thumbnail_sources: await thumbnails.evaluateAll((nodes: HTMLImageElement[]) => nodes.map((node) => node.getAttribute("src"))),
    slides_before: 1, slides_after: 2, returned_without_reload: true,
    scope: "real synthetic project/API/SDK save and decoded revision-scoped canvas/thumbnails; not brand or AI",
  }, null, 2));
});