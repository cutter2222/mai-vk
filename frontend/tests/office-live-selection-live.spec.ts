import { expect, test, type Frame, type Page } from "@playwright/test";

import { cleanupProjects, PROJECT_ANNOTATION } from "./helpers";

/**
 * Живая проверка правки выбранного слайда на стеке с настоящими ONLYOFFICE и моделью: сборка
 * варианта «Сбалансированный» через API, проект открыт в живом редакторе, на слайде 1 — ручная
 * правка с клавиатуры, в ленте редактора выбран слайд с сеткой карточек, в чат уходит «сделай не
 * N колонки, а 2». Пересобранный слайд должен встать в открытую копию, ручная правка слайда 1 —
 * остаться, редактор — открыться на том же слайде.
 *   LIVE_SLIDE_EDIT=1 PLAYWRIGHT_BASE_URL=https://izbox.ru playwright test office-live-selection-live --project=chromium
 */
test.use({ trace: "off" });
test.afterEach(async ({ page }) => cleanupProjects(page));

type Api = { getCurrentPage: () => number; getCountPages: () => number; goToPage: (i: number) => void; isDocumentModified: () => boolean };

async function editorFrame(page: Page): Promise<Frame> {
  for (let i = 0; i < 120; i++) {
    for (const f of page.frames()) {
      try {
        if (await f.evaluate(() => typeof (window as unknown as { PE?: { getController: (n: string) => { getApi: () => Api } } }).PE?.getController("Viewport").getApi().getCurrentPage === "function")) return f;
      } catch { /* кадр грузится */ }
    }
    await page.waitForTimeout(1000);
  }
  throw new Error("редактор не открылся");
}
const editor = <T,>(frame: Frame, fn: (api: Api) => T) => frame.evaluate(`(${fn.toString()})(window.PE.getController("Viewport").getApi())`) as Promise<T>;

test("выбранный в редакторе слайд правится из чата, ручная правка другого слайда остаётся", async ({ page, request }, info) => {
  test.skip(!process.env.PLAYWRIGHT_BASE_URL || !process.env.LIVE_SLIDE_EDIT, "нужны PLAYWRIGHT_BASE_URL и LIVE_SLIDE_EDIT");
  test.setTimeout(1_200_000);
  const note = (type: string, description: string) => { info.annotations.push({ type, description }); console.log(`${type}: ${description}`); };

  // 1. Сборка одного варианта через API — как после первого сообщения в чате.
  const templates = await (await request.get("/api/templates", { timeout: 30_000 })).json() as Array<{ template_id: string; name: string; status: string }>;
  const tpl = templates.find((t) => /VK Education/.test(t.name) && t.status === "succeeded")!;
  const project = await (await request.post("/api/projects", { data: { title: "Проверка правки слайда" }, timeout: 30_000 })).json() as { project_id: string };
  info.annotations.push({ type: PROJECT_ANNOTATION, description: project.project_id });
  const brief = { title: "Четыре принципа командной игры в футболе", purpose: "education", audience: "юные футболисты и их тренеры", goal: "объяснить четыре принципа командной игры: пас, открывание, прессинг, страховка", language: "ru" };
  // LIVE_SLIDE_EDIT_JOB — готовая сборка (повторный прогон без ожидания модели).
  let job = { job_id: process.env.LIVE_SLIDE_EDIT_JOB ?? "" };
  if (job.job_id) {
    const ready = await (await request.get(`/api/generations/${job.job_id}`, { timeout: 30_000 })).json() as { template_id: string; package_id: string };
    await request.patch(`/api/projects/${project.project_id}`, { data: { job_id: job.job_id, template_id: ready.template_id, package_id: ready.package_id }, timeout: 30_000 });
  } else {
    const pkg = await (await request.post("/api/content", { data: { file_ids: [], brief }, timeout: 30_000 })).json() as { package_id: string };
    await expect.poll(async () => (await (await request.get(`/api/content/${pkg.package_id}`, { timeout: 30_000 })).json()).status, { timeout: 180_000 }).toMatch(/succeeded|needs_review/);
    job = await (await request.post("/api/generations", { data: { schema_version: "1.2", template_id: tpl.template_id, package_id: pkg.package_id, settings: { variants: ["balanced"] } }, timeout: 30_000 })).json() as { job_id: string };
    await request.patch(`/api/projects/${project.project_id}`, { data: { job_id: job.job_id, template_id: tpl.template_id, package_id: pkg.package_id }, timeout: 30_000 });
    const started = Date.now();
    await expect.poll(async () => (await (await request.get(`/api/generations/${job.job_id}`, { timeout: 30_000 })).json()).status, { timeout: 600_000, intervals: [5000] }).toMatch(/succeeded|needs_review/);
    note("generation_s", String(Math.round((Date.now() - started) / 1000)));
  }
  note("job", job.job_id);
  const result = await (await request.get(`/api/generations/${job.job_id}`, { timeout: 30_000 })).json() as { variants: Array<{ variant_id: string; plan_artifact?: string; slide_count?: number }> };
  const variant = result.variants.find((v) => v.variant_id === "balanced")!;
  const plan = await (await request.get(`/api/generations/${job.job_id}/artifacts/${variant.plan_artifact}`, { timeout: 30_000 })).json() as { slides: Array<{ order: number; role?: string; title?: string; pattern_id: string; blocks?: Array<{ kind?: string }> }> };
  const slides = [...plan.slides].sort((a, b) => a.order - b.order);
  // Места сетки в плане — отдельные блоки текста (body_1, body_2 …), не пункты одного блока.
  const items = (s: (typeof slides)[number]) => (s.blocks ?? []).filter((b) => ["body", "bullets", "number"].includes(b.kind ?? "")).length;
  const grid = slides.map((s, i) => ({ i, n: items(s), cards: s.role === "cards" })).filter((s) => s.i > 0 && s.n >= 3);
  const targetIndex = (grid.find((s) => s.n >= 4) ?? grid.find((s) => s.cards) ?? grid[0] ?? { i: -1 }).i;
  expect(targetIndex, "в плане есть слайд с сеткой из трёх и больше элементов").toBeGreaterThan(0);
  const target = targetIndex + 1;
  const before = slides[targetIndex];
  note("target", `слайд ${target}: ${before.pattern_id}, элементов ${items(before)}, «${before.title ?? ""}»`);

  // 2. Проект в живом редакторе.
  const docResponse = page.waitForResponse((r) => r.url().endsWith("/api/office/documents") && r.request().method() === "POST", { timeout: 120_000 });
  await page.goto(`/project?id=${project.project_id}`);
  const docId = (await (await docResponse).json() as { id: string }).id;
  let frame = await editorFrame(page);
  await page.waitForTimeout(3000);

  // 3. Ручная правка слайда 1: двойной щелчок по заголовку, конец строки, приписка.
  await editor(frame, (api) => api.goToPage(0));
  await page.waitForTimeout(800);
  const frameBox = (await (await frame.frameElement()).boundingBox())!;
  const view = await frame.evaluate(() => { const r = document.getElementById("id_main_view")!.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height }; });
  const marker = "ручная правка";
  for (const [fx, fy] of [[0.25, 0.5], [0.25, 0.42], [0.3, 0.58]]) {
    await page.mouse.dblclick(frameBox.x + view.x + view.w * fx, frameBox.y + view.y + view.h * fy);
    await page.keyboard.press("End");
    await page.keyboard.type(` ${marker}`);
    await page.waitForTimeout(800);
    if (await editor(frame, (api) => api.isDocumentModified())) break;
  }
  const touched = await editor(frame, (api) => api.isDocumentModified());
  note("manual_edit", touched ? "слайд 1 изменён с клавиатуры" : "ручную правку сделать не удалось — проверяется путь без правок");
  await page.keyboard.press("Escape");

  // 4. Выбор слайда в редакторе — плашка в чате.
  await frame.evaluate((i) => (window as unknown as { PE: { getController: (n: string) => { getApi: () => Api } } }).PE.getController("Viewport").getApi().goToPage(i), target - 1);
  const chip = page.getByTestId("live-target");
  await expect(chip).toHaveText(`Слайд ${target} · Сбалансированный`, { timeout: 15_000 });
  note("chip", (await chip.textContent()) ?? "");

  // 5. Просьба про сетку. У повторно взятой сборки уже есть правки: ждём новую.
  type Edit = { result: string; change_note?: string; message?: string; new_revision?: number };
  const edits = async () => ((await (await request.get(`/api/generations/${job.job_id}`, { timeout: 30_000 })).json()).edits ?? []) as Edit[];
  const known = (await edits()).length;
  const instruction = `сделай не ${items(before)} колонки, а 2`;
  const sent = Date.now();
  await page.getByTestId("chat-input").fill(instruction);
  await page.getByTestId("chat-send").click();
  const card = page.getByTestId("chat-list").getByTestId("edit-card").last();
  await expect(card).toBeVisible({ timeout: 60_000 });
  // 6. Правка готова и перенесена: ревизия офисной копии выросла, редактор открылся снова.
  const revisionAtSend = (await (await request.get(`/api/office/documents/${docId}`, { timeout: 30_000 })).json()).revision as number;
  await expect.poll(async () => (await edits()).length, { timeout: 600_000, intervals: [3000] }).toBeGreaterThan(known);
  const edit = (await edits()).at(-1)!;
  note("edit", `${edit.result} за ${Math.round((Date.now() - sent) / 1000)} с: ${edit.change_note ?? edit.message ?? ""}`);
  expect(edit.result).toBe("applied");
  await expect.poll(async () => (await (await request.get(`/api/office/documents/${docId}`, { timeout: 30_000 })).json()).revision as number, { timeout: 180_000, intervals: [2000] }).toBeGreaterThan(revisionAtSend);
  note("applied_s", String(Math.round((Date.now() - sent) / 1000)));
  frame = await editorFrame(page);
  await expect.poll(() => editor(frame, (api) => api.getCurrentPage()), { timeout: 30_000 }).toBe(target - 1);
  await expect(page.getByText("Доступна другая версия презентации")).toHaveCount(0);

  // 7. В копии: пересобранный слайд на месте, ручная правка слайда 1 цела.
  const doc = await (await request.get(`/api/office/documents/${docId}`, { timeout: 30_000 })).json() as { revision: number };
  const objects = (await (await request.get(`/api/office/documents/${docId}/objects/${doc.revision}`, { timeout: 60_000 })).json()).objects as Array<{ slide: number; label: string }>;
  const labels = (n: number) => objects.filter((o) => o.slide === n).map((o) => o.label);
  note("slide_1", labels(1).join(" | ").slice(0, 300));
  note(`slide_${target}`, labels(target).join(" | ").slice(0, 400));
  if (touched) expect(labels(1).join(" ")).toContain(marker);
  const after = (await (await request.get(`/api/generations/${job.job_id}/artifacts/${`balanced/r${edit.new_revision}/plan.json`}`, { timeout: 30_000 })).json() as typeof plan).slides.sort((a, b) => a.order - b.order)[targetIndex];
  note("after", `${after.pattern_id}, элементов ${items(after)}, «${after.title ?? ""}»`);
});
