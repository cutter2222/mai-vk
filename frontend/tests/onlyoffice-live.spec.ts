import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";

const digest = (data: Buffer) => createHash("sha256").update(data).digest("hex");

// Opt-in against a real local Document Server. No LLM calls; source is an existing PPTX.
test("ONLYOFFICE opens a generated PPTX and closes without changing the AI source", async ({ page, request }) => {
  test.skip(!process.env.ONLYOFFICE_JOB_ID || !process.env.ONLYOFFICE_ARTIFACT, "Requires real ONLYOFFICE and a generated PPTX");
  test.setTimeout(240_000);
  page.setDefaultTimeout(30_000);
  const created = await request.post("/api/office/documents", { data: {
    job_id: process.env.ONLYOFFICE_JOB_ID,
    artifact: process.env.ONLYOFFICE_ARTIFACT,
  } });
  expect(created.ok()).toBeTruthy();
  const doc = await created.json() as { id: string; revision: number };
  const configPath = `/api/office/documents/${doc.id}/config`;
  await page.goto(`/office?id=${doc.id}`);
  await expect(page.locator("iframe")).toBeVisible({ timeout: 120_000 });
  await expect(page.getByText("Загружается редактор…")).toHaveCount(0, { timeout: 180_000 });
  await expect(page.getByText("Ошибка ONLYOFFICE.", { exact: false })).toHaveCount(0);
  if (process.env.ONLYOFFICE_EDIT_SMOKE === "1") {
    const frame = page.frameLocator("iframe");
    await frame.locator("#id-toolbar-btn-add-slide").click();
    await page.waitForTimeout(2000);
  }
  await page.screenshot({ path: "/tmp/mai-vk-onlyoffice-open.png", fullPage: true });
  await page.getByRole("button", { name: "Действия с презентацией" }).click();
  await page.getByRole("menuitem", { name: "Завершить редактирование" }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByText("Сессия закрыта.", { exact: false })).toBeVisible({ timeout: 60_000 });
  if (process.env.ONLYOFFICE_EDIT_SMOKE === "1") {
    const saved = await (await request.get(`/api/office/documents/${doc.id}`)).json();
    expect(saved.revision).toBeGreaterThan(doc.revision);
    expect(saved.error).toBeNull();
    const download = await request.get(`/api/office/documents/${doc.id}/download/${saved.revision}`);
    expect(download.ok()).toBeTruthy();
    const original = await request.get(`/api/office/documents/${doc.id}/download/0`);
    expect(digest(await download.body())).not.toBe(digest(await original.body()));
    const reopened = page.waitForResponse((response) => response.url().endsWith(configPath) && response.request().method() === "POST");
    await page.getByRole("button", { name: "Открыть снова" }).click();
    const config = await (await reopened).json();
    expect(config.config.document.url).toContain(`/source/${saved.revision}?`);
    await expect(page.locator("iframe")).toBeVisible();
    await expect(page.getByText("Загружается редактор…")).toHaveCount(0, { timeout: 90_000 });
    await page.getByRole("button", { name: "Действия с презентацией" }).click();
    await page.getByRole("menuitem", { name: "Завершить редактирование" }).click();
    await page.keyboard.press("Escape");
    await expect(page.getByText("Сессия закрыта.", { exact: false })).toBeVisible({ timeout: 60_000 });
    const exact = await request.get(`/api/office/documents/${doc.id}/download/${saved.revision}`);
    expect(digest(await exact.body())).toBe(digest(await download.body()));
  }
  const source = await request.get(`/api/generations/${process.env.ONLYOFFICE_JOB_ID}/artifacts/${process.env.ONLYOFFICE_ARTIFACT}`);
  const initial = await request.get(`/api/office/documents/${doc.id}/download/0`);
  expect(source.ok()).toBeTruthy();
  expect(initial.ok()).toBeTruthy();
  expect(digest(await initial.body())).toBe(digest(await source.body()));
});