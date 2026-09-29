import { expect, test, type Page } from "@playwright/test";

// Шаблон и текст задачи есть — сборка стартует сама: без вопроса о режиме, выбора назначения и
// кнопки. Варианты собираются по очереди: первый открыт, остальные видны с загрузкой.
const BRIEF = { purpose: "", title: "", audience: "", goal: "", language: "ru", tone: "", must_include: [], avoid: [] };
const SETTINGS = { mode: "range", min: 10, max: 15, exact: 12, variants: ["compact", "balanced", "detailed"], contextual: true, images: false, seed: null, force: false };
const TEXT = "Презентация «Эволюция тактики в современном футболе» для тренеров и спортивных аналитиков: схемы 4-4-2 и 4-3-3, тики-така, высокий прессинг, роль данных.";

async function setup(page: Page, templateId: string | null) {
  const state = { generations: [] as Record<string, unknown>[], imports: 0 };
  const project: Record<string, unknown> = {
    schema_version: "1.5", project_id: "auto-test", title: "Новая презентация", template_id: templateId, package_id: null, job_id: null, chosen_variant: null,
    created_at: "2026-09-24T10:00:00Z", updated_at: "2026-09-24T10:00:00Z", brief: BRIEF, settings: SETTINGS, files: [], events: [],
  };
  await page.route("**/api/projects/auto-test", (r) => {
    if (r.request().method() === "PATCH") Object.assign(project, r.request().postDataJSON());
    return r.fulfill({ json: project });
  });
  await page.route("**/api/projects/auto-test/events", (r) => r.fulfill({ json: { ...r.request().postDataJSON(), event_id: `evt_${Date.now()}${Math.random()}`, at: new Date().toISOString() } }));
  await page.route("**/api/brief", (r) => r.fulfill({ json: {
    schema_version: "1.0", source: "model", intent: "describe", understood: ["title", "audience"],
    brief: { title: "Эволюция тактики в современном футболе", audience: "Тренеры и спортивные аналитики" },
  } }));
  await page.route("**/api/content", (r) => { state.imports++; return r.fulfill({ status: 202, json: { package_id: "pkg_auto", job_id: "job_import", cached: false } }); });
  await page.route("**/api/content/pkg_auto", (r) => r.fulfill({ json: { status: "succeeded", job_id: "job_import", package: { package_id: "pkg_auto", blocks: [], facts: [], datasets: [], assets: [] } } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [{ template_id: "tpl_lib", name: "Фирменный.pptx", status: "succeeded", created_at: "2026-09-20" }] }));
  await page.route(/\/api\/templates\/tpl_(lib|auto)$/, (r) => r.fulfill({ json: { status: "succeeded", job_id: "job_tpl", name: "Фирменный.pptx", previews: [], profile: { assets: [] } } }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: false } }));
  await page.route("**/api/generations", (r) => { state.generations.push(r.request().postDataJSON()); return r.fulfill({ status: 202, json: { job_id: "job_auto" } }); });
  await page.route("**/api/generations/job_auto", (r) => r.fulfill({ json: {
    job_id: "job_auto", status: "running", stage: "plan", created_at: new Date().toISOString(), metrics: {}, execution_mode: { mode: "real", layers: {} },
    progress: { percent: 20, message: "Собираю первый вариант" },
    variants: ["compact", "balanced", "detailed"].map((variant_id) => ({ variant_id, status: variant_id === "balanced" ? "running" : "pending", revision: 1, artifacts: {} })),
  } }));
  return state;
}

async function send(page: Page, text: string) {
  await page.getByTestId("chat-input").fill(text);
  await page.getByTestId("chat-send").click();
}

test("шаблон есть, пришёл текст — сборка стартует сама", async ({ page }) => {
  const state = await setup(page, "tpl_auto");
  await page.goto("/project?id=auto-test");
  await send(page, TEXT);
  const chat = page.getByTestId("chat-list");
  await expect(chat).toContainText("Понял задачу: «Эволюция тактики в современном футболе», аудитория — тренеры и спортивные аналитики. Собираю презентацию: сначала сбалансированный вариант, следом компактный и подробный.");
  await expect.poll(() => state.generations.length).toBe(1);
  expect(state.generations[0].settings).toMatchObject({ design_mode: "mixed", variants: ["compact", "balanced", "detailed"] });
  expect((state.generations[0].settings as Record<string, unknown>).slide_count).toBeUndefined();
  // Ни вопроса о режиме, ни карточки с назначением и кнопкой, ни «Прочитал…».
  await expect(chat).not.toContainText("Как оформить");
  await expect(chat.getByTestId("brief-card")).toHaveCount(0);
  await expect(chat.getByTestId("content-card")).toHaveCount(0);
  await expect(page.getByTestId("generation-progress")).toContainText("Собираю первый вариант");
  await expect(page.getByTestId("generation-timer")).toBeVisible();
});

test("шаблона нет — просьба выбрать, выбор шаблона запускает сборку", async ({ page }) => {
  const state = await setup(page, null);
  await page.goto("/project?id=auto-test");
  await send(page, TEXT);
  const chat = page.getByTestId("chat-list");
  await expect(chat).toContainText("Выберите шаблон оформления вверху справа — и я сразу начну собирать.");
  await expect.poll(() => state.imports).toBe(1);
  expect(state.generations).toHaveLength(0);
  await page.getByTestId("template-menu").click();
  await page.getByTestId("template-option-tpl_lib").click();
  await expect(chat).toContainText("Выбрал шаблон «Фирменный». Собираю презентацию: сначала сбалансированный вариант, следом компактный и подробный.");
  await expect.poll(() => state.generations.length).toBe(1);
  expect(state.generations[0]).toMatchObject({ template_id: "tpl_lib", package_id: "pkg_auto" });
});

test("первый вариант открыт, остальные с загрузкой; доделанный вариант не подменяет открытый", async ({ page }) => {
  const opened: string[] = [];
  const ready = new Set(["balanced"]);
  await page.route("**/api/projects/variants-test", (r) => r.fulfill({ json: {
    schema_version: "1.5", project_id: "variants-test", title: "Варианты", template_id: "tpl_v", package_id: "pkg_v", job_id: "job_v", chosen_variant: null,
    created_at: "2026-09-24T10:00:00Z", updated_at: "2026-09-24T10:00:00Z", brief: BRIEF, settings: SETTINGS, files: [], events: [],
  } }));
  await page.route("**/api/projects/variants-test/events", (r) => r.fulfill({ json: { ...r.request().postDataJSON(), event_id: `evt_${Date.now()}`, at: new Date().toISOString() } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/generations/job_v", (r) => r.fulfill({ json: {
    job_id: "job_v", status: "running", stage: "plan", created_at: new Date().toISOString(), metrics: {}, execution_mode: { mode: "real", layers: {} },
    progress: { percent: 50, message: "Готово 1 из 3, ещё 2 варианта собираются в фоне" },
    variants: ["compact", "balanced", "detailed"].map((variant_id) => ready.has(variant_id)
      ? { variant_id, status: "ready", revision: 1, slide_count: 10, artifacts: { pptx: `${variant_id}/r1/deck.pptx` } }
      : { variant_id, status: variant_id === "compact" ? "running" : "pending", revision: 1, artifacts: {} }),
  } }));
  const doc = (artifact: string) => ({ id: `doc-${artifact.split("/")[0]}`, source: `job_v/${artifact}`, title: "T", revision: 0, active_key: "key", error: null, revisions: [] });
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: true } }));
  await page.route("**/api/office/documents", (r) => { const artifact = r.request().postDataJSON().artifact as string; opened.push(artifact); return r.fulfill({ json: doc(artifact) }); });
  await page.route(/\/api\/office\/documents\/doc-\w+$/, (r) => r.fulfill({ json: doc(`${r.request().url().split("doc-").pop()}/r1/deck.pptx`) }));
  await page.route(/\/api\/office\/documents\/doc-\w+\/config$/, (r) => r.fulfill({ json: { script_url: "/office-variants-sdk.js", config: {} } }));
  await page.route("**/office-variants-sdk.js", (r) => r.fulfill({ contentType: "application/javascript", body: `
    window.DocsAPI = { DocEditor: function(id, config) {
      const frame = document.createElement('iframe'); document.getElementById(id).appendChild(frame);
      setTimeout(() => config.events.onDocumentReady(), 0);
      this.requestClose = () => config.events.onRequestClose();
      this.destroyEditor = () => frame.remove();
    }};
  ` }));

  await page.goto("/project?id=variants-test");
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
  // Выбор варианта — выпадающий список рядом с шаблоном: открыт первый собранный.
  const switcher = page.getByTestId("office-variants");
  await expect(switcher).toBeVisible();
  await expect(switcher).toHaveAttribute("data-value", "balanced");
  await expect(switcher).toContainText("Сбалансированный");
  await switcher.click();
  await expect(page.getByTestId("office-variant-compact")).toHaveAttribute("data-state", "building");
  await expect(page.getByTestId("office-variant-detailed")).toHaveAttribute("data-state", "building");
  await expect(page.getByTestId("office-variant-compact")).toBeDisabled();
  await expect(page.getByTestId("office-variant-balanced")).toHaveAttribute("aria-checked", "true");
  await page.keyboard.press("Escape");
  expect(opened).toEqual(["balanced/r1/deck.pptx"]);

  // «Компактный» доделался: становится доступен, но открытую презентацию не подменяет.
  ready.add("compact");
  await switcher.click();
  await expect(page.getByTestId("office-variant-compact")).toHaveAttribute("data-state", "ready", { timeout: 10000 });
  await expect(switcher).toHaveAttribute("data-value", "balanced");
  await expect(page.getByText("Доступна другая версия презентации")).toHaveCount(0);
  expect(opened).toEqual(["balanced/r1/deck.pptx"]);

  await page.getByTestId("office-variant-compact").click();
  await expect.poll(() => opened).toEqual(["balanced/r1/deck.pptx", "compact/r1/deck.pptx"]);
  await expect(switcher).toHaveAttribute("data-value", "compact");
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
});

test("итог — о первом варианте со временем от запроса, остальные — строками со своим временем", async ({ page }) => {
  // Числа задания «Футбол» 27.09: сбалансированный готов через 3 мин 14 с, компактный собирался
  // 1 мин 52 с, подробный 4 мин 31 с, всё задание с проверками — 15 мин 8 с.
  const created = "2026-09-27T20:59:15.000Z";
  const job: Record<string, unknown> = { status: "running", stage: "plan" };
  const variants: Record<string, Record<string, unknown>> = {
    compact: { variant_id: "compact", status: "pending", revision: 1, artifacts: {} },
    balanced: { variant_id: "balanced", status: "running", revision: 1, artifacts: {}, stages: [{ stage: "plan", status: "running", started_at: "2026-09-27T20:59:37.000Z" }] },
    detailed: { variant_id: "detailed", status: "pending", revision: 1, artifacts: {} },
  };
  await page.route("**/api/projects/lead-test", (r) => r.fulfill({ json: {
    schema_version: "1.5", project_id: "lead-test", title: "Футбол", template_id: "tpl_v", package_id: "pkg_v", job_id: "job_lead", chosen_variant: null,
    created_at: created, updated_at: created, brief: BRIEF, settings: SETTINGS, files: [],
    events: [{ event_id: "evt_job", at: created, role: "assistant", kind: "job_card", job_id: "job_lead" }],
  } }));
  await page.route("**/api/projects/lead-test/events", (r) => r.fulfill({ json: { ...r.request().postDataJSON(), event_id: `evt_${Date.now()}`, at: new Date().toISOString() } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: false } }));
  await page.route("**/api/generations/job_lead", (r) => r.fulfill({ json: {
    job_id: "job_lead", ...job, created_at: created, metrics: { totals: { duration_ms: 908_000 } }, execution_mode: { mode: "real", layers: {} },
    progress: { percent: 40, message: "Собираю первый вариант" },
    variants: ["compact", "balanced", "detailed"].map((id) => variants[id]),
    versions: { app: "0.1.0", contracts: "1.13", analyzer: { name: "template_analyzer", version: "0.4.6" },
      skills: [{ name: "variant_planner", version: "0.5.1" }, { name: "story_planner", version: "0.3.2" }],
      models: [{ role: "llm", name: "qwen3.8-27b", params_b: 27, license: "Apache-2.0" }] },
  } }));

  await page.goto("/project?id=lead-test");
  const card = page.getByTestId("job-card");
  const lines = card.locator(".job-variant-line");
  // Пока первый не готов: что происходит сейчас и строки в порядке сборки.
  await expect(card.getByTestId("job-summary")).toHaveText("Раскладываю содержание по слайдам");
  await expect(lines).toHaveText(["Сбалансированный — собирается", "Компактный — в очереди", "Подробный — в очереди"]);
  await expect(page.getByTestId("generation-timer")).toBeVisible();

  // Сбалансированный готов: итог о нём со временем от запроса, остальные собираются; таймер
  // общего времени сверху уходит.
  Object.assign(variants.balanced, { status: "ready", slide_count: 12, ready_at: "2026-09-27T21:02:29.000Z", artifacts: { pptx: "balanced/r1/deck.pptx" } });
  Object.assign(variants.compact, { status: "running", stages: [{ stage: "plan", status: "running", started_at: "2026-09-27T21:05:50.000Z" }] });
  await expect(card.getByTestId("job-summary")).toHaveText("Собрал сбалансированный вариант из 12 слайдов за 3 мин 14 с.", { timeout: 10_000 });
  await expect(lines).toHaveText(["Компактный — собирается", "Подробный — в очереди"]);
  await expect(page.getByTestId("generation-progress")).toBeVisible();
  await expect(page.getByTestId("generation-timer")).toHaveCount(0);

  // Все готовы: у каждого своё время сборки, общего времени задания в ленте нет.
  Object.assign(variants.compact, { status: "needs_review", slide_count: 10, ready_at: "2026-09-27T21:07:42.000Z", artifacts: { pptx: "compact/r1/deck.pptx" } });
  Object.assign(variants.detailed, { status: "needs_review", slide_count: 14, ready_at: "2026-09-27T21:10:21.000Z", artifacts: { pptx: "detailed/r1/deck.pptx" },
    stages: [{ stage: "plan", status: "done", started_at: "2026-09-27T21:05:50.000Z", duration_ms: 250_000 }] });
  Object.assign(job, { status: "needs_review", stage: "done", finished_at: "2026-09-27T21:14:23.000Z" });
  await expect(lines).toHaveText(["Компактный — собран за 1 мин 52 с, 10 слайдов", "Подробный — собран за 4 мин 31 с, 14 слайдов"], { timeout: 10_000 });
  await expect(card.getByTestId("job-summary")).toHaveText("Собрал сбалансированный вариант из 12 слайдов за 3 мин 14 с.");
  await expect(card).not.toContainText("15 мин");

  // В подробностях — чем сделан результат: модель и версии скиллов.
  await card.getByTestId("job-details").click();
  const versions = card.getByTestId("job-versions");
  await expect(versions.getByTestId("job-model-llm")).toHaveText("модель: qwen3.8-27b · 27B · Apache-2.0");
  await expect(versions.getByTestId("job-skill-variant_planner")).toHaveText("variant_planner0.5.1");
  await expect(versions).toContainText("анализатор 0.4.6");
});
