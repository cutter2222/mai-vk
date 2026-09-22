import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";

const digest = (data: Buffer) => createHash("sha256").update(data).digest("hex");

async function ready(page: Page) {
  await expect(page.locator("iframe")).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText("Загружается редактор…")).toHaveCount(0, { timeout: 180_000 });
  await expect(page.getByText("Ошибка ONLYOFFICE.", { exact: false })).toHaveCount(0);
  await expect(page.frameLocator("iframe").locator("#id-toolbar-btn-add-slide")).toBeVisible();
}

async function close(page: Page) {
  await page.getByRole("button", { name: "Действия с презентацией" }).click();
  await page.getByRole("menuitem", { name: "Завершить редактирование" }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByText("Сессия закрыта.", { exact: false })).toBeVisible({ timeout: 90_000 });
}

async function preview(request: APIRequestContext, base: string, revision: number) {
  const url = `${base}/preview/${revision}`;
  const response = await request.get(url, { timeout: 120_000 });
  expect(response.ok()).toBeTruthy();
  const manifest = await response.json() as { revision: number; slides: string[]; ratio: number };
  expect(manifest.revision).toBe(revision);
  expect(manifest.ratio).toBeGreaterThan(0);
  const hashes: string[] = [];
  // Request the last page first: page availability must not depend on request order.
  for (const name of [...manifest.slides].reverse()) {
    const image = await request.get(`${url}/${name}`);
    expect(image.ok()).toBeTruthy();
    expect(image.headers()["content-type"]).toContain("image/png");
    expect(image.headers()["cache-control"]).toContain("immutable");
    const data = await image.body();
    expect(data.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
    hashes.unshift(digest(data));
  }
  return { url, manifest, hashes };
}

// Explicit opt-in; a reused/generated/user-owned document is rejected BEFORE opening SDK.
test("isolated SDK save/reopen publishes a new preview revision", async ({ page, request }, testInfo) => {
  test.skip(!process.env.ONLYOFFICE_SDK_DOCUMENT_ID || !process.env.ONLYOFFICE_SDK_SHA256,
    "Run scripts/seed_office_sdk.py first; requires a real local ONLYOFFICE stack");
  test.setTimeout(420_000);
  const id = process.env.ONLYOFFICE_SDK_DOCUMENT_ID!;
  expect(id).toMatch(/^[a-f0-9]{32}$/);
  const base = `/api/office/documents/${id}`;
  const initialResponse = await request.get(base);
  expect(initialResponse.ok()).toBeTruthy();
  const initial = await initialResponse.json();
  expect(initial.source).toMatch(/^sdk-sandbox\/[a-f0-9]{32}$/);
  expect(initial.revision).toBe(0);
  expect(initial.active_key).toBeNull();
  expect(initial.error).toBeNull();
  expect(initial.revisions).toHaveLength(1);
  const originalResponse = await request.get(`${base}/download/0`);
  expect(originalResponse.ok()).toBeTruthy();
  const original = await originalResponse.body();
  expect(digest(original)).toBe(process.env.ONLYOFFICE_SDK_SHA256);
  const before = await preview(request, base, 0);
  expect(before.manifest.slides).toHaveLength(1);

  const opened = page.waitForResponse((r) => r.url().endsWith(`${base}/config`) && r.request().method() === "POST");
  await page.goto(`/office?id=${id}`);
  const firstConfig = await (await opened).json();
  expect(firstConfig.config.document.url).toContain("/source/0?");
  await ready(page);
  // Normal editor UI only: no internal SDK calls, Automation API or plugin injection.
  await page.frameLocator("iframe").locator("#id-toolbar-btn-add-slide").click();
  await close(page);
  const savedResponse = await request.get(base);
  expect(savedResponse.ok()).toBeTruthy();
  const saved = await savedResponse.json();
  expect(saved.revision).toBeGreaterThan(0);
  expect(saved.active_key).toBeNull();
  expect(saved.error).toBeNull();
  const download = await request.get(`${base}/download/${saved.revision}`);
  expect(download.ok()).toBeTruthy();
  const updated = await download.body();
  expect(digest(updated)).not.toBe(digest(original));
  const savedPath = testInfo.outputPath("saved.pptx");
  await writeFile(savedPath, updated);
  await testInfo.attach("saved.pptx", { path: savedPath, contentType: "application/vnd.openxmlformats-officedocument.presentationml.presentation" });
  const after = await preview(request, base, saved.revision);
  expect(after.url).not.toBe(before.url);
  expect(after.manifest.slides).toHaveLength(2);

  const reopened = page.waitForResponse((r) => r.url().endsWith(`${base}/config`) && r.request().method() === "POST");
  await page.getByRole("button", { name: "Открыть снова" }).click();
  const config = await (await reopened).json();
  expect(config.config.document.url).toContain(`/source/${saved.revision}?`);
  expect(config.config.document.key).not.toBe(firstConfig.config.document.key);
  await ready(page);
  await page.screenshot({ path: testInfo.outputPath("reopened.png"), fullPage: true });
  await close(page);

  const exact = await request.get(`${base}/download/${saved.revision}`);
  expect(exact.ok()).toBeTruthy();
  expect(digest(await exact.body())).toBe(digest(updated));
  const unchanged = await request.get(`${base}/download/0`);
  expect(unchanged.ok()).toBeTruthy();
  expect(digest(await unchanged.body())).toBe(digest(original));
  expect(await preview(request, base, 0)).toEqual(before);
  const finalResponse = await request.get(base);
  expect(finalResponse.ok()).toBeTruthy();
  const final = await finalResponse.json();
  expect(final.active_key).toBeNull();
  expect(final.error).toBeNull();
  const reportPath = testInfo.outputPath("report.json");
  await writeFile(reportPath, JSON.stringify({
    document_id: id, source: initial.source, before, after,
    original_sha256: digest(original), saved_sha256: digest(updated),
    saved_revision: saved.revision, final_revision: final.revision,
    original_unchanged: true, saved_revision_immutable: true, reopened_with_new_key: true,
    scope: "synthetic SDK save/reopen and revision-scoped preview; not brand preservation or AI",
  }, null, 2));
  await testInfo.attach("report.json", { path: reportPath, contentType: "application/json" });
});
