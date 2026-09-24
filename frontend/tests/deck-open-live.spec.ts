import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { cleanupProjects, createProject, PPTX_MIME, projectIdAfterAction } from "./helpers";

/**
 * Замер на живом стеке: «Открыть как презентацию» → слайды в редакторе ONLYOFFICE. Секундомер
 * чата («Презентация открыта за N с») и время от нажатия до итога записываются в аннотации
 * теста; сразу после открытия уходит правка слайда из чата — она ждёт разбора в фоне и
 * применяется с честной фразой. Нужен PPTX, который уже есть в библиотеке сервера (те же
 * байты не добавят второй шаблон):
 *   DECK_OPEN_FILE=… PLAYWRIGHT_BASE_URL=https://izbox.ru playwright test deck-open-live --project=chromium
 * DECK_OPEN_BUSY=N — перед открытием через API запускаются N чужих сборок трёх вариантов по
 * уникальным темам (кэш модели для них холодный), чтобы очередь была занята; через
 * DECK_OPEN_BUSY_WAIT_MS (20 с) после их старта открывается презентация.
 */
const FILE = process.env.DECK_OPEN_FILE;

test.use({ trace: "off" });
test.afterEach(async ({ page }) => cleanupProjects(page));

test("открытие готовой презентации: слайды сразу, разбор и правка из чата — в фоне", async ({ page, request }, info) => {
  test.skip(!process.env.PLAYWRIGHT_BASE_URL || !FILE, "нужны PLAYWRIGHT_BASE_URL и DECK_OPEN_FILE");
  test.setTimeout(900_000);
  const note = (type: string, description: string) => { info.annotations.push({ type, description }); console.log(`${type}: ${description}`); };

  const busyJobs: string[] = [];
  const busyCount = Number(process.env.DECK_OPEN_BUSY || 0);
  if (busyCount > 0) {
    // Чужие сборки трёх вариантов: темы уникальны, поэтому смысловой план и варианты идут
    // моделью заново (кэш холодный) и держат воркеры генерации.
    const templates = await (await request.get("/api/templates", { timeout: 30_000 })).json() as Array<{ template_id: string; name: string; status: string }>;
    const tpl = templates.find((t) => t.status === "succeeded" && !/Проверка вставки/.test(t.name)) ?? templates[0];
    for (let i = 0; i < busyCount; i++) {
      const brief = { title: `Нагрузка очереди ${Date.now()}-${i}`, purpose: "product", audience: "руководители", goal: "показать занятость очереди" };
      const pkg = await (await request.post("/api/content", { data: { file_ids: [], brief }, timeout: 30_000 })).json() as { package_id: string };
      const busy = await (await request.post("/api/generations", { data: {
        schema_version: "1.2", template_id: tpl.template_id, package_id: pkg.package_id,
        settings: { variants: ["compact", "balanced", "detailed"] },
      }, timeout: 30_000 })).json() as { job_id: string };
      busyJobs.push(busy.job_id);
    }
    note("busy_jobs", busyJobs.join(" "));
    // Сборки должны успеть занять воркеры: ждём, пока они пойдут.
    for (const id of busyJobs) {
      await expect.poll(async () => (await (await request.get(`/api/jobs/${id}`, { timeout: 15_000 })).json()).status, { timeout: 180_000 }).toBe("running");
    }
    await page.waitForTimeout(Number(process.env.DECK_OPEN_BUSY_WAIT_MS || 20_000));
    const stages = await Promise.all(busyJobs.map(async (id) => (await (await request.get(`/api/jobs/${id}`, { timeout: 15_000 })).json()).stage));
    note("busy_stages_at_open", stages.join(" "));
  }

  await createProject(page);
  const data = readFileSync(FILE!);
  const [chooser] = await Promise.all([page.waitForEvent("filechooser"), page.getByTestId("chat-attach").click()]);
  await chooser.setFiles({ name: path.basename(FILE!), mimeType: PPTX_MIME, buffer: data });
  await projectIdAfterAction(page);
  // Файл загружен: вопрос из ленты (не черновой, пока файл едет) с кнопками ответа.
  await expect(page.locator('[data-testid^="template-question-stg_"]')).toHaveCount(0, { timeout: 300_000 });
  const answer = page.getByTestId("answer-deck").last();
  await expect(answer).toBeVisible();
  const clicked = Date.now();
  await answer.click();
  const opened = page.getByTestId("chat-list").getByTestId("deck-opened");
  await expect(opened).toBeVisible({ timeout: 180_000 });
  note("open_wall_ms", String(Date.now() - clicked));
  note("open_chat", (await opened.textContent()) ?? "");
  await expect(page.frameLocator("iframe").first().locator("body")).toBeVisible();

  const projectId = new URL(page.url()).searchParams.get("id")!;
  const project = await (await request.get(`/api/projects/${projectId}`, { timeout: 30_000 })).json() as { job_id: string };
  note("job", project.job_id);

  // Правка из чата сразу после открытия: плана ещё нет, сервер ставит её за разбором.
  await page.getByTestId("chat-input").fill("на слайде 2 замени заголовок на «Проверка правки из чата»");
  await page.getByTestId("chat-send").click();
  const edit = page.getByTestId("chat-list").getByTestId("edit-card").last();
  await expect(edit).toBeVisible({ timeout: 60_000 });
  // Плана ещё нет: сервер ставит правку за разбором и говорит, когда применит.
  await expect(edit).toContainText(/Разбираю презентацию(, правку применю через|: очередь занята)/, { timeout: 30_000 });
  note("edit_deferred_phrase", (await edit.textContent()) ?? "");
  note("edit_sent_ms", String(Date.now() - clicked));
  const charts = page.getByTestId("chat-list").getByTestId("deck-charts");
  const chartsAt = charts.waitFor({ timeout: 300_000 }).then(() => Date.now() - clicked, () => null);
  await expect(page.getByTestId("chat-list").getByTestId("edit-note")).toBeVisible({ timeout: 600_000 });
  note("edit_applied_ms", String(Date.now() - clicked));
  note("edit_note", (await page.getByTestId("edit-note").last().textContent()) ?? "");
  const chartsMs = await chartsAt;
  if (chartsMs != null) {
    note("charts_ms", String(chartsMs));
    note("charts_chat", (await charts.textContent()) ?? "");
  }
  const result = await (await request.get(`/api/generations/${project.job_id}`, { timeout: 30_000 })).json() as {
    variants: Array<{ ready_at?: string; revisions: Array<{ revision: number; created_at: string }> }>;
    created_at: string; metrics: { timeline: { first_file_ready_ms?: number } };
  };
  const variant = result.variants[0];
  note("revisions", variant.revisions.map((r) => `r${r.revision}@${r.created_at}`).join(" "));
  note("ready_at", `${variant.ready_at} (first_file_ready_ms ${result.metrics.timeline.first_file_ready_ms})`);
  expect(variant.ready_at && variant.revisions[0] && Math.abs(Date.parse(variant.ready_at) - Date.parse(variant.revisions[0].created_at))).toBeLessThan(1000);
  for (const id of busyJobs) {
    const busy = await (await request.get(`/api/jobs/${id}`, { timeout: 30_000 })).json() as { status: string; stage: string };
    note(`busy_${id}`, `${busy.status} ${busy.stage}`);
  }
});
