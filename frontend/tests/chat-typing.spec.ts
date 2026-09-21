import { expect, test, type Page } from "@playwright/test";

const reply = "Не нашёл в сообщении ничего про презентацию. Опишите задачу одной фразой: «сделай презентацию про запуск сервиса умных уведомлений для руководителей, чтобы одобрили пилот» — и перетащите шаблон PPTX и материалы.";
const replies = (page: Page) => page.locator(".chat-msg > .chat-assistant-text");
const greeting = "Добавьте шаблон презентации или выберите уже ранее загруженный.";

async function setup(page: Page, history = false, settleGreeting = true) {
  const project = {
    project_id: "chat-typing-test", title: "Новая презентация", files: [], brief: {}, settings: {},
    created_at: "2026-09-21T12:00:00Z",
    events: history ? [{ event_id: "history", at: "2026-01-01T00:00:00Z", role: "assistant", kind: "text", text: reply }] as Record<string, unknown>[] : [] as Record<string, unknown>[],
  };
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: false } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  let releaseProject!: () => void;
  const projectReady = new Promise<void>((resolve) => { releaseProject = resolve; });
  await page.route("**/api/projects/chat-typing-test", async (r) => {
    await projectReady;
    await r.fulfill({ json: project });
  });
  await page.route("**/api/projects/chat-typing-test/events", async (r) => {
    const event = { ...r.request().postDataJSON(), event_id: `saved-${project.events.length}`, at: new Date().toISOString() };
    project.events.push(event);
    await r.fulfill({ json: event });
  });
  await page.route("**/api/brief", (r) => r.fulfill({ json: { understood: [], brief: {}, source: "heuristic" } }));
  await page.route("**/api/chat", (r) => {
    const event = { event_id: `saved-${project.events.length}`, at: new Date().toISOString(), role: "assistant", kind: "text", text: reply };
    project.events.push(event);
    return r.fulfill({ json: { reply, options: [], source: "model", event } });
  });
  await page.goto("/project?id=chat-typing-test");
  await page.clock.install({ time: new Date("2026-09-21T12:00:00Z") });
  await page.clock.pauseAt(new Date("2026-09-21T12:01:00Z"));
  releaseProject();
  await expect(page.getByTestId("chat-input")).toBeVisible();
  if (settleGreeting) await page.clock.runFor(3000);
  return project;
}

async function send(page: Page, text = "Привет") {
  await page.getByTestId("chat-input").fill(text);
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("msg-user").last()).toContainText(text);
}

test("assistant types replies sequentially, survives saved IDs and restores history without replay", async ({ page }, testInfo) => {
  const project = await setup(page);
  await send(page);
  await expect.poll(() => project.events.length).toBe(2);
  await expect(page.getByTestId("assistant-typing")).toBeVisible();
  await expect(replies(page)).toHaveCount(0);
  await page.clock.runFor(800);
  const first = replies(page).first();
  await expect(first).toContainText("Не нашёл");
  expect((await first.textContent())!.length).toBeLessThan(reply.length);
  await expect(page.locator('[data-typing="true"]')).toHaveCount(1);
  for (const message of [page.getByTestId("msg-user").last(), page.getByTestId("msg-assistant").last()]) {
    const time = message.getByTestId("message-time");
    await expect(time).toHaveAttribute("datetime", /T/);
    await expect(time).toContainText(/\d{2}:\d{2}/);
    const content = await message.locator(":scope > :first-child").boundingBox();
    const stamp = await time.boundingBox();
    expect(stamp!.y).toBeGreaterThanOrEqual(content!.y + content!.height);
  }
  await page.screenshot({ path: testInfo.outputPath("assistant-typing.png"), animations: "disabled" });

  // Ввод не блокируется визуальной печатью; второй ответ ждёт первого.
  await send(page, "Ещё вопрос");
  await expect.poll(() => project.events.length).toBe(4);
  await expect(replies(page)).toHaveCount(1);
  await page.clock.runFor(1600);
  await expect(first).toHaveText(reply);
  await expect(replies(page)).toHaveCount(1);
  await page.clock.runFor(700);
  await expect(replies(page)).toHaveCount(2);
  expect((await replies(page).last().textContent())!.length).toBeLessThan(reply.length);
  await page.clock.runFor(2000);
  await expect(replies(page).last()).toHaveText(reply);
  await expect(page.getByTestId("assistant-typing")).toHaveCount(0);
  await expect(page.locator('[data-typing="true"]')).toHaveCount(0);

  // Гидратация Next.js после reload тоже использует таймеры.
  await page.clock.resume();
  await page.reload();
  await expect(replies(page)).toHaveText([reply, reply]);
  await expect(page.getByTestId("template-greeting")).toHaveText(greeting);
  await expect(page.getByTestId("assistant-typing")).toHaveCount(0);
});

test("existing messages are immediate and reduced motion skips new text animation", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await setup(page, true);
  await expect(replies(page)).toHaveText(reply);
  await expect(page.getByTestId("template-greeting")).toHaveText(greeting);
  await send(page);
  await expect(replies(page)).toHaveText([reply, reply]);
  await expect(page.getByTestId("assistant-typing")).toHaveCount(0);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.clock.runFor(1000);
  await expect(replies(page)).toHaveText([reply, reply]);
  await expect(page.getByTestId("assistant-typing")).toHaveCount(0);
});

test("greeting types before replies without blocking template controls", async ({ page }) => {
  const project = await setup(page, false, false);
  const intro = page.getByTestId("template-greeting");
  await expect(intro).toHaveAttribute("aria-busy", "true");
  await expect(page.getByTestId("template-menu")).toBeEnabled();
  await page.clock.runFor(600);
  expect((await intro.textContent())!.length).toBeGreaterThan(0);
  expect((await intro.textContent())!.length).toBeLessThan(greeting.length);
  await send(page);
  await expect.poll(() => project.events.length).toBe(2);
  await expect(replies(page)).toHaveCount(0);
  await page.clock.runFor(4000);
  await expect(intro).toHaveText(greeting);
  await expect(replies(page)).toHaveText(reply);
  await expect(page.getByTestId("assistant-typing")).toHaveCount(0);
  await expect(page.getByTestId("message-time").first()).toHaveAttribute("datetime", "2026-09-21T12:00:00Z");
});

test("typing indicator covers a pending request and disappears after a failed request reply", async ({ page }) => {
  await setup(page);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/brief", async (r) => {
    await pending;
    await r.fulfill({ status: 500, json: { detail: "Unavailable" } });
  });
  await send(page);
  await page.clock.runFor(3000);
  await expect(page.getByTestId("assistant-typing")).toBeVisible();
  await expect(replies(page)).toHaveCount(0);
  release();
  await expect(page.getByTestId("chat-input")).toHaveValue("");
  await expect(page.getByTestId("chat-send")).not.toHaveAttribute("data-loading", "true");
  await page.clock.runFor(2600);
  await expect(replies(page)).toHaveText(reply);
  await expect(page.getByTestId("assistant-typing")).toHaveCount(0);
});