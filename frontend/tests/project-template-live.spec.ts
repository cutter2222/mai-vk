import { expect, test } from "@playwright/test";

test("selected template leaves the real project canvas empty until generation", async ({ page }) => {
  test.skip(!process.env.ONLYOFFICE_TEMPLATE_PROJECT_ID, "Opt-in read-only local project check");
  test.setTimeout(120000);
  await page.goto(`/project?id=${process.env.ONLYOFFICE_TEMPLATE_PROJECT_ID}`);
  await expect(page.getByTestId("preview-empty")).toContainText("Здесь появится ваша презентация");
  await expect(page.getByTestId("preview-pane").locator("iframe, img")).toHaveCount(0);
  await expect(page.getByTestId("slide-counter")).toHaveCount(0);
  await page.screenshot({ path: test.info().outputPath("template-empty-canvas.png"), fullPage: true });
});
