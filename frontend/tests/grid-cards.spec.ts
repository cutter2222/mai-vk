import { expect, test, type Page } from "@playwright/test";

/**
 * Сетки главной и библиотеки: карточка «новая» одного размера с карточками и до, и после
 * загрузки, на экране и на телефоне; пока список едет — заглушки карточек, а не пустота.
 */
const PROJECTS = [1, 2].map((i) => ({
  project_id: `p${i}`, title: `Проект ${i}`, created_at: "2026-09-21", updated_at: "2026-09-21", job_status: "succeeded",
  job_id: `job${i}`, template_id: null, package_id: null, chosen_variant: null, files_count: 0, template_name: "Фирменный.pptx", thumbnail_url: null, slide_count: 12,
}));
const TEMPLATES = [1, 2].map((i) => ({
  template_id: `t${i}`, name: `Шаблон ${i}.pptx`, status: "succeeded", created_at: "2026-09-21", slide_count: 30, patterns_count: 8, colors: ["#123456", "#abcdef"], previews: [],
}));

async function box(page: Page, selector: string) {
  const rect = await page.locator(selector).first().boundingBox();
  expect(rect).not.toBeNull();
  return rect!;
}

for (const viewport of [{ name: "desktop", width: 1280, height: 800 }, { name: "phone", width: 390, height: 844 }]) {
  for (const [path, api, newId, cardPrefix] of [["/", "**/api/projects", "new-project", "project-card-"], ["/templates", "**/api/templates", "new-template", "template-card-"]] as const) {
    test(`${path} на ${viewport.name}: карточка «новая» не меняет размер, до данных — заглушки`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      let release!: () => void;
      const gate = new Promise<void>((resolve) => { release = resolve; });
      await page.route(api, async (route) => {
        if (route.request().url().includes("/api/templates/")) return route.fallback();
        await gate;
        return route.fulfill({ json: path === "/" ? PROJECTS : TEMPLATES });
      });
      await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: false } }));
      await page.goto(path);
      await expect(page.getByTestId("card-skeleton")).toHaveCount(3);
      const before = await box(page, `[data-testid="${newId}"]`);
      const skeleton = await box(page, '[data-testid="card-skeleton"]');
      await page.screenshot({ path: testInfo.outputPath(`${viewport.name}-loading.png`), fullPage: true });
      release();
      const cards = page.locator(`.grid-card[data-testid^="${cardPrefix}"]`);
      await expect(cards).toHaveCount(2);
      await expect(page.getByTestId("card-skeleton")).toHaveCount(0);
      const after = await box(page, `[data-testid="${newId}"]`);
      const card = await box(page, `.grid-card[data-testid^="${cardPrefix}"]`);
      await page.screenshot({ path: testInfo.outputPath(`${viewport.name}-loaded.png`), fullPage: true });
      // Размер карточки «новая» один и тот же до и после данных и равен размеру соседей.
      expect(Math.abs(after.width - before.width)).toBeLessThanOrEqual(1);
      expect(Math.abs(after.height - before.height)).toBeLessThanOrEqual(1);
      expect(Math.abs(after.height - card.height)).toBeLessThanOrEqual(2);
      expect(Math.abs(before.height - skeleton.height)).toBeLessThanOrEqual(2);
    });
  }
}
