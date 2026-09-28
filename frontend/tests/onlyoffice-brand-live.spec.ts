import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";

const digest = (data: Buffer) => createHash("sha256").update(data).digest("hex");

// No interception or SDK internals. Signed URLs must never be retained in traces.
test.use({ trace: "off" });
for (const explicitSave of [false, true]) {
test(`brand copy ${explicitSave ? "explicit save" : "immediate close without toolbar Save"} and reopen records revisions for preservation audit`, async ({ page, request }, testInfo) => {
  test.skip(!process.env.ONLYOFFICE_BRAND_DOCUMENT_ID || !process.env.ONLYOFFICE_BRAND_SHA256,
    "Requires a fresh seed_office_sdk.py --source copy and a live stack");
  test.setTimeout(600_000);
  const id = process.env.ONLYOFFICE_BRAND_DOCUMENT_ID!;
  const replacement = {
    slide: Number(process.env.ONLYOFFICE_BRAND_SLIDE ?? 3),
    before: process.env.ONLYOFFICE_BRAND_OLD ?? "Редактируемый слайд-разделитель",
    after: process.env.ONLYOFFICE_BRAND_NEW ?? "Обновлённый слайд-разделитель",
  };
  expect(Number.isInteger(replacement.slide)).toBe(true);
  expect(replacement.slide).toBeGreaterThan(0);
  expect(replacement.before.length).toBeGreaterThan(0);
  expect(replacement.after).not.toBe(replacement.before);
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
    return digest(bytes);
  };
  const ready = async () => {
    await expect(page.locator("iframe")).toBeVisible({ timeout: 120_000 });
    await expect(page.getByTestId("office-loading")).toHaveCount(0, { timeout: 180_000 });
    await expect(page.frameLocator("iframe").locator("#id-toolbar-btn-add-slide")).toBeVisible();
    await expect(page.getByText("Ошибка ONLYOFFICE.", { exact: false })).toHaveCount(0);
  };
  const close = async () => {
    await page.getByRole("button", { name: "Завершить и сохранить", exact: true }).click();
    await expect(page.getByText("Сессия закрыта.", { exact: false })).toBeVisible({ timeout: 120_000 });
    const doc = await metadata();
    expect(doc.active_key).toBeNull();
    expect(doc.error).toBeNull();
    return doc;
  };
  const initial = await metadata();
  expect(initial.source).toMatch(/^sdk-brand\/[a-f0-9]{32}$/);
  expect(initial.revision).toBe(0);
  expect(initial.active_key).toBeNull();
  expect(initial.error).toBeNull();
  expect(initial.revisions).toHaveLength(1);
  const originalHash = await download(0, "before.pptx");
  expect(originalHash).toBe(process.env.ONLYOFFICE_BRAND_SHA256);
  const sdkResponse = process.env.ONLYOFFICE_SDK_SHA256
    ? page.waitForResponse(r => /\/sdkjs\/slide\/sdk-all\.js(?:\?|$)/.test(r.url()), { timeout: 120_000 })
    : null;
  await page.goto(`/office?id=${id}`);
  await ready();
  const sdkHash = sdkResponse ? digest(await (await sdkResponse).body()) : null;
  if (sdkHash) expect(sdkHash).toBe(process.env.ONLYOFFICE_SDK_SHA256);
  await page.screenshot({ path: testInfo.outputPath("opened.png"), fullPage: true });
  const noop = await close();
  const noopHash = await download(noop.revision, "noop.pptx");
  expect(noopHash).toBe(originalHash);
  await page.getByRole("button", { name: "Открыть снова" }).click();
  await ready();
  // The replace panel is normal user-facing editor UI, not Automation API.
  const frame = page.frameLocator("iframe");
  const whatsNew = frame.getByRole("button", { name: "OK", exact: true });
  if (await whatsNew.isVisible()) await whatsNew.click();
  await frame.locator("#id-toolbar-btn-add-slide").focus();
  await page.keyboard.press("Control+h");
  const search = frame.getByPlaceholder("Поиск", { exact: true });
  const replace = frame.getByPlaceholder("Заменить на", { exact: true });
  await search.fill(replacement.before);
  await search.press("Enter");
  await expect(search).toHaveValue(replacement.before);
  await expect(frame.locator("#search-adv-replace-all")).toBeEnabled({ timeout: 30_000 });
  await replace.fill(replacement.after);
  await expect(replace).toHaveValue(replacement.after);
  await expect(search).toHaveValue(replacement.before);
  await frame.locator("#search-adv-replace-all").click({ timeout: 30_000 });
  await expect(frame.getByText("1 элементов успешно заменено.", { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("text-edited.png"), fullPage: true });
  let toolbarSave: { revision: number; sha256: string } | null = null;
  if (explicitSave) {
    // What's New can appear after editing, even if it was absent when reopening.
    if (await whatsNew.isVisible()) await whatsNew.click();
    // SDK versions may place Save in the header and leave the old toolbar button hidden.
    await frame.getByRole("button", { name: /^Сохранить(?:\s*\(|$)/ }).click({ timeout: 30_000 });
    await expect.poll(async () => (await metadata()).revision, { timeout: 120_000 }).toBeGreaterThan(noop.revision);
    const checkpoint = await metadata();
    expect(checkpoint.error).toBeNull();
    toolbarSave = { revision: checkpoint.revision,
      sha256: await download(checkpoint.revision, "toolbar-saved.pptx") };
    expect(toolbarSave.sha256).not.toBe(originalHash);
  }
  const saved = await close();
  expect(saved.revision).toBeGreaterThan(noop.revision);
  const savedHash = await download(saved.revision, "saved.pptx");
  expect(savedHash).not.toBe(originalHash);
  const configResponse = page.waitForResponse(r => r.url().endsWith(`${base}/config`) && r.request().method() === "POST");
  await page.getByRole("button", { name: "Открыть снова" }).click();
  const config = await (await configResponse).json();
  expect(config.config.document.url).toContain(`/source/${saved.revision}?`);
  await ready();
  await page.screenshot({ path: testInfo.outputPath("reopened.png"), fullPage: true });
  const final = await close();
  expect(await download(final.revision, "reopened.pptx")).toBe(savedHash);
  expect(await download(0, "original-after.pptx")).toBe(originalHash);
  await writeFile(testInfo.outputPath("report.json"), JSON.stringify({
    document_id: id, original_sha256: originalHash, noop_sha256: noopHash,
    saved_sha256: savedHash, saved_revision: saved.revision,
    noop_revision: noop.revision, final_revision: final.revision,
    active_key: final.active_key, error: final.error,
    explicit_save: explicitSave, normalizations: final.normalizations ?? [],
    sdk_sha256: sdkHash,
    browser_version: page.context().browser()?.version(),
    browser_channel: process.env.PLAYWRIGHT_CHROMIUM_CHANNEL ?? "bundled",
    reopen_mode: "same-page",
    toolbar_save: toolbarSave,
    replacement,
    original_unchanged: true, reopened_saved_revision: true,
    scope: "Live UI save/reopen; semantic and visual preservation require the separate PPTX audit",
  }, null, 2));
});
}