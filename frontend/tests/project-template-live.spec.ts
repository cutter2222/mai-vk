import { expect, test } from "@playwright/test";

test("real ONLYOFFICE opens the selected template on a project without generation", async ({ page }) => {
  test.skip(!process.env.ONLYOFFICE_TEMPLATE_PROJECT_ID, "Opt-in read-only local project check");
  test.setTimeout(120000);
  await page.goto(`/project?id=${process.env.ONLYOFFICE_TEMPLATE_PROJECT_ID}`);
  const viewer = page.getByTestId("template-source-viewer");
  await expect(viewer.locator("iframe")).toBeVisible({ timeout: 30000 });
  await expect(viewer.locator(".office-loading")).toHaveCount(0, { timeout: 90000 });
  await expect(viewer.getByRole("alert")).toHaveCount(0);
  await expect(viewer.frameLocator("iframe").getByText(/Слайд 1 из \d+/)).toBeVisible();
  await page.screenshot({ path: test.info().outputPath("template-onlyoffice.png"), fullPage: true });
});
