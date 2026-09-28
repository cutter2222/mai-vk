import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";

test.use({ trace: "off" });
test("saved precision sandbox reopens unchanged in a fresh browser", async ({ page, request }, testInfo) => {
  const id = process.env.ONLYOFFICE_PRECISION_REOPEN_ID;
  test.skip(!id, "Requires an already saved precision sandbox, not a user document");
  test.setTimeout(300_000);
  expect(id).toMatch(/^[a-f0-9]{32}$/);
  const base = `/api/office/documents/${id}`;
  const metadata = async () => {
    const response = await request.get(base);
    expect(response.ok()).toBeTruthy();
    return response.json();
  };
  const download = async (revision: number, name: string) => {
    const response = await request.get(`${base}/download/${revision}`);
    expect(response.ok()).toBeTruthy();
    const bytes = await response.body();
    await writeFile(testInfo.outputPath(name), bytes);
    return createHash("sha256").update(bytes).digest("hex");
  };
  const initial = await metadata();
  expect(initial.source).toMatch(/^sdk-brand\/[a-f0-9]{32}$/);
  expect(initial.revision).toBeGreaterThan(0);
  expect(initial.active_key).toBeNull();
  expect(initial.error).toBeNull();
  const original = await download(0, "original.pptx");
  expect(original).toBe(process.env.ONLYOFFICE_BRAND_SHA256);
  const saved = await download(initial.revision, "saved.pptx");
  expect(saved).toBe(process.env.ONLYOFFICE_SAVED_SHA256);
  const configuration = page.waitForResponse(r => r.url().endsWith(`${base}/config`) && r.request().method() === "POST");
  const sdk = page.waitForResponse(r => /\/sdkjs\/slide\/sdk-all\.js(?:\?|$)/.test(r.url()));
  await page.goto(`/office?id=${id}`);
  expect((await (await configuration).json()).config.document.url).toContain(`/source/${initial.revision}?`);
  await expect(page.getByTestId("office-loading")).toHaveCount(0, { timeout: 180_000 });
  await expect(page.frameLocator("iframe").locator("#id-toolbar-btn-add-slide")).toBeVisible({ timeout: 120_000 });
  expect(createHash("sha256").update(await (await sdk).body()).digest("hex")).toBe(process.env.ONLYOFFICE_SDK_SHA256);
  await page.screenshot({ path: testInfo.outputPath("reopened.png") });
  await page.getByRole("button", { name: "Завершить и сохранить", exact: true }).click();
  await expect(page.getByText("Сессия закрыта.", { exact: false })).toBeVisible({ timeout: 120_000 });
  const final = await metadata();
  expect(final.active_key).toBeNull();
  expect(final.error).toBeNull();
  expect(await download(final.revision, "reopened.pptx")).toBe(saved);
  expect(await download(0, "original-after.pptx")).toBe(original);
  await writeFile(testInfo.outputPath("report.json"), JSON.stringify({
    document_id: id, saved_revision: initial.revision, final_revision: final.revision,
    original_sha256: original, saved_sha256: saved, active_key: final.active_key,
    error: final.error, reopen_mode: "fresh-browser", normalizations: final.normalizations ?? [],
  }, null, 2));
});