import { expect, test } from "@playwright/test";

test("скриншоты главной и шаблонов", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  await page.goto("/");
  await expect(page.getByText("Мои презентации").first()).toBeVisible();
  await expect(page.getByTestId("health")).toContainText("Сервис работает");
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/home.png`, fullPage: true });
  await page.goto("/templates");
  await expect(page.getByRole("heading", { name: "Шаблоны" })).toBeVisible();
  await expect(page.locator('[data-testid^="template-card-"]').first()).toBeVisible();
  await page.waitForTimeout(800);
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/templates.png`, fullPage: true });
  await page.locator('[data-testid^="template-card-"]').first().click();
  await expect(page.getByTestId("template-tabs")).toBeVisible();
  await page.waitForTimeout(800);
  if (process.env.SHOT_DIR) await page.screenshot({ path: `${process.env.SHOT_DIR}/template-detail.png`, fullPage: true });
  expect(errors, errors.join("\n")).toEqual([]);
});
