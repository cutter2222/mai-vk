import { expect, test, type Page } from "@playwright/test";

async function checkLayout(page: Page) {
  const header = page.getByTestId("catalog-header");
  const title = await header.getByRole("heading", { level: 1 }).boundingBox();
  const search = await header.getByRole("textbox").boundingBox();
  const filter = await header.getByRole("combobox").boundingBox();
  expect(title).not.toBeNull();
  expect(search).not.toBeNull();
  expect(filter).not.toBeNull();
  expect(Math.abs(title!.y + title!.height / 2 - search!.y - search!.height / 2)).toBeLessThan(2);
  expect(Math.abs(search!.y - filter!.y)).toBeLessThan(2);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(header.getByRole("textbox")).toBeVisible();
  await expect(header.getByRole("combobox")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

test("шаблоны: компактная шапка, поиск и фильтр без рекламного текста и счётчика", async ({ page }) => {
  await page.route("**/api/templates", (route) => route.fulfill({ json: [
    { template_id: "ready", name: "Бренд.pptx", status: "succeeded", created_at: "2026-09-21" },
    { template_id: "failed", name: "Отчёт.pptx", status: "failed", created_at: "2026-09-21" },
  ] }));
  await page.goto("/templates");
  await expect(page.getByTestId("template-card-ready")).toBeVisible();
  await expect(page.getByText("Библиотека дизайн-систем", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Фирменный стиль начинается здесь/)).toHaveCount(0);
  await expect(page.getByText(/^\d+ в библиотеке$/)).toHaveCount(0);
  await checkLayout(page);
  await page.getByRole("combobox", { name: "Статус шаблонов" }).click();
  await page.getByRole("option", { name: "С ошибкой", exact: true }).click();
  await expect(page.getByTestId("template-card-ready")).toHaveCount(0);
  await expect(page.getByTestId("template-card-failed")).toBeVisible();
  await page.getByRole("textbox", { name: "Поиск шаблонов" }).fill("бренд");
  await expect(page.getByText(/По вашему запросу шаблонов нет/)).toBeVisible();
  await expect(page.getByTestId("new-template")).toBeVisible();
});

test("главная: та же шапка, поиск и все статусы презентаций", async ({ page }) => {
  const statuses = [null, "queued", "running", "succeeded", "needs_review", "failed", "canceled", null];
  await page.route("**/api/projects", (route) => route.fulfill({ json: statuses.map((status, i) => ({
    project_id: `p${i}`, title: i === 3 ? "Годовой отчёт" : `Проект ${i}`,
    created_at: "2026-09-21", updated_at: "2026-09-21", job_status: status,
    job_id: i === 0 ? null : `job${i}`, template_id: null, package_id: null,
    chosen_variant: null, files_count: 0, template_name: null, thumbnail_url: null, slide_count: null,
  })) }));
  await page.goto("/");
  const cards = page.locator('[data-testid^="project-card-"]');
  await expect(cards).toHaveCount(8);
  await checkLayout(page);
  const filter = page.getByRole("combobox", { name: "Статус презентаций" });
  for (const [label, count] of [["Черновики", 1], ["В работе", 3], ["Готовые", 2], ["С ошибкой", 1], ["Отменённые", 1], ["Все презентации", 8]] as const) {
    await filter.click();
    await page.getByRole("option", { name: label, exact: true }).click();
    await expect(cards).toHaveCount(count);
  }
  const search = page.getByRole("textbox", { name: "Поиск презентаций" });
  await search.fill("  ГОДОВОЙ  ");
  await expect(cards).toHaveCount(1);
  await expect(page.getByTestId("project-card-p3")).toBeVisible();
  await filter.click();
  await page.getByRole("option", { name: "Черновики", exact: true }).click();
  await expect(cards).toHaveCount(0);
  await expect(page.getByText(/По вашему запросу презентаций нет/)).toBeVisible();
  await expect(page.getByTestId("new-project")).toBeVisible();
});