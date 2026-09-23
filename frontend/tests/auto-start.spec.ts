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
  await expect(chat).toContainText("Понял задачу: «Эволюция тактики в современном футболе», аудитория — тренеры и спортивные аналитики. Собираю презентацию: сначала один вариант, ещё 2 — следом.");
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
  await expect(chat).toContainText("Выбрал шаблон «Фирменный». Собираю презентацию: сначала один вариант, ещё 2 — следом.");
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
  const switcher = page.getByTestId("office-variants");
  await expect(switcher).toBeVisible();
  await expect(page.getByTestId("office-variant-compact")).toHaveAttribute("data-state", "building");
  await expect(page.getByTestId("office-variant-detailed")).toHaveAttribute("data-state", "building");
  await expect(switcher.locator('input[value="compact"]')).toBeDisabled();
  await expect(switcher.locator('input[value="balanced"]')).toBeChecked();
  expect(opened).toEqual(["balanced/r1/deck.pptx"]);

  // «Компактный» доделался: становится доступен, но открытую презентацию не подменяет.
  ready.add("compact");
  await expect(page.getByTestId("office-variant-compact")).toHaveAttribute("data-state", "ready", { timeout: 10000 });
  await expect(switcher.locator('input[value="balanced"]')).toBeChecked();
  await expect(page.getByText("Доступна другая версия презентации")).toHaveCount(0);
  expect(opened).toEqual(["balanced/r1/deck.pptx"]);

  await page.getByTestId("office-variant-compact").click();
  await expect.poll(() => opened).toEqual(["balanced/r1/deck.pptx", "compact/r1/deck.pptx"]);
  await expect(switcher.locator('input[value="compact"]')).toBeChecked();
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
});
