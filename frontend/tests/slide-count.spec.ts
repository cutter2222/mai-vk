import { expect, test, type Page } from "@playwright/test";

// Число слайдов по умолчанию — не просьба пользователя: в чате не называется и в генерацию не
// уходит. Если содержания меньше, чем просили, задание собирает короче и говорит об этом фразой.
const BRIEF = { purpose: "report", title: "Итоги квартала", audience: "", goal: "", language: "ru", tone: "", must_include: [], avoid: [] };
const SETTINGS = { design_mode: "template_only", mode: "range", min: 10, max: 15, exact: 12, variants: ["compact", "balanced", "detailed"], contextual: true, images: false, seed: null, force: false };

async function setup(page: Page, settings: Record<string, unknown>, jobId: string | null = null) {
  const requests: Record<string, unknown>[] = [];
  const project = {
    schema_version: "1.5", project_id: "count-test", title: "Итоги квартала", template_id: "tpl_count", package_id: "pkg_count", job_id: jobId, chosen_variant: null,
    created_at: "2026-09-24T10:00:00Z", updated_at: "2026-09-24T10:00:00Z", brief: BRIEF, settings, files: [],
    events: [
      { event_id: "evt_brief", at: "2026-09-24T10:00:01Z", role: "assistant", kind: "brief_card", understood: [], missing_purpose: false },
      ...(jobId ? [{ event_id: "evt_job", at: "2026-09-24T10:00:02Z", role: "assistant", kind: "job_card", job_id: jobId }] : []),
    ],
  };
  await page.route("**/api/projects/count-test", (r) => {
    if (r.request().method() === "PATCH") Object.assign(project, r.request().postDataJSON());
    return r.fulfill({ json: project });
  });
  await page.route("**/api/projects/count-test/events", (r) => r.fulfill({ json: { ...r.request().postDataJSON(), event_id: `evt_${Date.now()}`, at: new Date().toISOString() } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/templates/tpl_count", (r) => r.fulfill({ json: { status: "succeeded", job_id: "job_tpl", name: "Шаблон", previews: [], profile: { assets: [] } } }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: false } }));
  await page.route("**/api/capabilities", (r) => r.fulfill({ json: { contracts_version: "1.9", execution_mode: { mode: "real", layers: {} }, features: {}, limits: { max_upload_mb: 50, max_content_files: 20, slide_count_max: 60 } } }));
  await page.route("**/api/generations", (r) => { requests.push(r.request().postDataJSON()); return r.fulfill({ status: 202, json: { job_id: "job_new" } }); });
  return requests;
}

test("без просьбы о количестве: в чате нет «10–15», в запросе нет slide_count", async ({ page }) => {
  const requests = await setup(page, SETTINGS);
  await page.goto("/project?id=count-test");
  const card = page.getByTestId("brief-card");
  await expect(card).toContainText("Соберу презентацию в трёх вариантах вёрстки.");
  await expect(card).not.toContainText("10–15");
  await card.getByTestId("generate").click();
  await expect.poll(() => requests.length).toBe(1);
  expect((requests[0].settings as Record<string, unknown>).slide_count).toBeUndefined();
});

test("просили 12 — число в чате и в запросе", async ({ page }) => {
  const requests = await setup(page, { ...SETTINGS, mode: "exact", exact: 12 });
  await page.goto("/project?id=count-test");
  await expect(page.getByTestId("brief-card")).toContainText("Соберу ровно 12 слайдов в трёх вариантах вёрстки.");
  await page.getByTestId("generate").click();
  await expect.poll(() => requests.length).toBe(1);
  expect((requests[0].settings as Record<string, unknown>).slide_count).toEqual({ exact: 12 });
});

test("содержания меньше, чем просили: варианты собраны, фраза о нехватке", async ({ page }) => {
  await setup(page, { ...SETTINGS, mode: "exact", exact: 12 }, "job_short");
  const message = "Просили 12 слайдов — содержания хватило на 6–7. Добавьте материалы или расскажите о теме подробнее, и я соберу больше.";
  await page.route("**/api/generations/job_short", (r) => r.fulfill({ json: {
    job_id: "job_short", status: "needs_review", stage: "done", created_at: "2026-09-24T10:00:00Z", finished_at: "2026-09-24T10:03:00Z",
    metrics: { totals: { duration_ms: 180000 } }, execution_mode: { mode: "real", layers: {} }, progress: { percent: 100, message: "" },
    variants: [["compact", 6], ["balanced", 7], ["detailed", 7]].map(([variant_id, slide_count]) => ({ variant_id, status: "ready", revision: 1, slide_count, artifacts: {}, ...(variant_id === "balanced" ? { ready_at: "2026-09-24T10:01:30Z" } : {}) })),
    warnings: [{ code: "slide_count_short", message }],
  } }));
  await page.goto("/project?id=count-test");
  const job = page.getByTestId("job-card");
  await expect(job.getByTestId("job-summary")).toHaveText("Собрал сбалансированный вариант из 7 слайдов за 1 мин 30 с.");
  await expect(job.getByTestId("job-short")).toHaveText(message);
  await expect(job).not.toContainText("не собрался");
});
