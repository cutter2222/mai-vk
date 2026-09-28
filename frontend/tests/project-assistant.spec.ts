import { expect, test } from "@playwright/test";

import { mockRouter } from "./helpers";

test("template-only project stays empty without an editor session", async ({ page }) => {
  await page.route("**/api/projects/template-view-test", (r) => r.fulfill({ json: {
    project_id: "template-view-test", title: "Тест", template_id: "tpl-view", brief: {}, settings: {}, files: [], events: [],
  } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/templates/tpl-view", (r) => r.fulfill({ json: { template_id: "tpl-view", status: "succeeded", name: "Шаблон", previews: ["slide-01.png"] } }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: true } }));
  const doc = { id: "view-copy", revision: 0, active_key: null, error: null };
  await page.route("**/api/office/projects/template-view-test/template", (r) => r.fulfill({ json: doc }));
  await page.route("**/api/office/documents/view-copy", (r) => r.fulfill({ json: doc }));
  await page.route("**/api/office/documents/view-copy/objects/0", (r) => r.fulfill({ json: { revision: 0, objects: [] } }));
  let documents = 0;
  await page.route("**/api/office/documents", (r) => { documents++; return r.fulfill({ status: 500 }); });
  await page.goto("/project?id=template-view-test");
  await expect(page.getByTestId("preview-empty")).toContainText("Здесь появится ваша презентация");
  await expect(page.getByTestId("project-office")).toHaveCount(0);
  await expect(page.getByTestId("slide-counter")).toHaveCount(0);
  await expect(page.locator("iframe")).toHaveCount(0);
  await expect(page.getByTestId("office-loading")).toHaveCount(0);
  await expect(page.getByText("Рабочая копия шаблона", { exact: false })).toHaveCount(0);
  expect(documents).toBe(0);
});

test("questions about a generated deck call chat, not office edit, and survive reload", async ({ page }) => {
  await mockRouter(page);
  const events: Record<string, unknown>[] = [];
  await page.route("**/api/projects/assistant-test", (r) => r.fulfill({ json: {
    project_id: "assistant-test", title: "Тест", job_id: "job_chat", brief: {}, settings: {}, files: [], events,
  } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: true } }));
  await page.route("**/api/generations/job_chat", (r) => r.fulfill({ json: {
    job_id: "job_chat", status: "succeeded", stage: "done", metrics: { totals: { duration_ms: 1000 } }, execution_mode: { mode: "real", layers: {} },
    variants: [{ variant_id: "compact", status: "ready", revision: 1, artifacts: { pptx: "compact/r1/deck.pptx" } }],
  } }));
  await page.route("**/api/office/documents", (r) => r.fulfill({ status: 503, json: { error: { message: "Редактор занят" } } }));
  await page.route("**/api/projects/assistant-test/events", (r) => {
    const event = { ...r.request().postDataJSON(), event_id: `event-${events.length}`, at: new Date().toISOString() };
    events.push(event); return r.fulfill({ json: event });
  });
  let chats = 0;
  let edits = 0;
  await page.route("**/api/office/documents/*/edit", (r) => { edits++; return r.fulfill({ status: 500 }); });
  await page.route("**/api/chat", (r) => {
    chats++;
    expect(r.request().postDataJSON().project_id).toBe("assistant-test");
    const text = chats === 1 ? "Могу объяснить состояние проекта." : "Дальше можно править слайды.";
    const event = { event_id: `reply-${chats}`, at: new Date().toISOString(), role: "assistant", kind: "text", text };
    events.push(event);
    return r.fulfill({ json: { reply: event.text, event, options: ["Что дальше?"], source: "model" } });
  });
  await page.goto("/project?id=assistant-test");
  await expect(page.getByTestId("project-office")).toBeVisible();
  await page.getByTestId("chat-input").fill("Что ты умеешь?");
  await page.getByTestId("chat-send").click();
  await expect(page.getByText("Могу объяснить состояние проекта.")).toBeVisible();
  // Подсказка — готовый ответ: уходит сразу, поле ввода остаётся пустым.
  await page.getByTestId("chat-suggestions").getByRole("button").click();
  await expect.poll(() => chats).toBe(2);
  await expect(page.getByTestId("chat-input")).toHaveValue("");
  await expect(page.getByTestId("msg-user").filter({ hasText: "Что дальше?" })).toBeVisible();
  await expect(page.getByText("Дальше можно править слайды.")).toBeVisible();
  expect(edits).toBe(0);
  await page.reload();
  await expect(page.getByText("Могу объяснить состояние проекта.")).toBeVisible();
});

test("first published variant opens while other variants generate, without reopening on completion", async ({ page }) => {
  let finished = false;
  let opens = 0;
  await page.route("**/api/projects/async-test", (r) => r.fulfill({ json: {
    project_id: "async-test", title: "Асинхронная сборка", job_id: "job_async", brief: {}, settings: {}, files: [], events: [],
  } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: true } }));
  await page.route("**/api/generations/job_async", (r) => r.fulfill({ json: {
    job_id: "job_async", status: finished ? "succeeded" : "running", stage: finished ? "done" : "compose",
    metrics: { totals: { duration_ms: 1000 } }, execution_mode: { mode: "real", layers: {} },
    variants: [{ variant_id: "compact", revision: 1, artifacts: { pptx: "compact/r1/deck.pptx" } },
      { variant_id: "detailed", revision: 1, artifacts: finished ? { pptx: "detailed/r1/deck.pptx" } : {} }],
  } }));
  const doc = { id: "async-doc", revision: 0, active_key: null, revisions: [] };
  await page.route("**/api/office/documents", (r) => { opens++; return r.fulfill({ json: doc }); });
  await page.route("**/api/office/documents/async-doc", (r) => r.fulfill({ json: doc }));
  await page.route("**/api/office/documents/async-doc/preview/0", (r) => r.fulfill({ json: { revision: 0, slides: ["slide-01.png"], ratio: 16 / 9 } }));
  await page.route("**/api/office/documents/async-doc/config", (r) => r.fulfill({ json: { script_url: "/async-sdk.js", config: {} } }));
  await page.route("**/async-sdk.js", (r) => r.fulfill({ contentType: "application/javascript", body: `
    window.DocsAPI = { DocEditor: function(id, config) {
      const frame = document.createElement('iframe'); document.getElementById(id).appendChild(frame);
      setTimeout(() => config.events.onDocumentReady(), 0); this.destroyEditor = () => frame.remove();
    }};
  ` }));
  await page.goto("/project?id=async-test");
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
  await expect(page.getByText("Остальные варианты ещё собираются", { exact: false })).toBeVisible();
  finished = true;
  await expect(page.getByText("Остальные варианты ещё собираются", { exact: false })).toHaveCount(0, { timeout: 20000 });
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
  await expect(page.locator("iframe")).toHaveCount(1);
  expect(opens).toBe(1);
});

// Сообщение сохраняется сразу, а бриф извлекается долго: к моменту вопроса ассистенту временный
// ID уже заменён серверным, и в /api/chat должен уйти именно сохранённый event_id.
test("delayed brief extraction still sends the saved event ID to /api/chat", async ({ page }) => {
  const savedAt = "2026-09-21T09:00:00.000Z";
  const events: Record<string, unknown>[] = [];
  const project = { project_id: "assistant-id-test", title: "Новая презентация", template_id: null, brief: {}, settings: {}, files: [], events };
  await page.route("**/api/projects/assistant-id-test", async (r) => {
    if (r.request().method() === "PATCH") Object.assign(project, r.request().postDataJSON());
    await r.fulfill({ json: project });
  });
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: false } }));
  await page.route("**/api/projects/assistant-id-test/events", (r) => {
    const event = { ...r.request().postDataJSON(), event_id: `event-${events.length}`, at: savedAt };
    events.push(event);
    return r.fulfill({ json: event });
  });
  let releaseBrief!: () => void;
  const briefHeld = new Promise<void>((resolve) => { releaseBrief = resolve; });
  await page.route("**/api/brief", async (r) => {
    await briefHeld;
    await r.fulfill({ json: { understood: [], brief: {}, source: "heuristic" } });
  });
  const chatIds: string[] = [];
  await page.route("**/api/chat", (r) => {
    chatIds.push(r.request().postDataJSON().event_id);
    const event = { event_id: "reply", at: savedAt, role: "assistant", kind: "text", text: "Уточните задачу презентации." };
    events.push(event);
    return r.fulfill({ json: { reply: event.text, event, options: [], source: "model" } });
  });
  await page.goto("/project?id=assistant-id-test");
  await page.getByTestId("chat-input").fill("Привет");
  await page.getByTestId("chat-send").click();
  // Серверная запись уже заменила временную (время сообщения — серверное), а бриф ещё не отвечен.
  await expect(page.getByTestId("msg-user").last().getByTestId("message-time")).toHaveAttribute("datetime", savedAt);
  expect(events.map((e) => e.event_id)).toEqual(["event-0"]);
  expect(chatIds).toEqual([]);
  releaseBrief();
  await expect(page.getByText("Уточните задачу презентации.")).toBeVisible();
  expect(chatIds).toEqual(["event-0"]);
  await page.reload();
  await expect(page.getByText("Уточните задачу презентации.")).toBeVisible();
  expect(chatIds).toEqual(["event-0"]);
});

test("a message that failed to save never reaches /api/chat", async ({ page }) => {
  await page.route("**/api/projects/assistant-unsaved-test", (r) => r.fulfill({ json: {
    project_id: "assistant-unsaved-test", title: "Новая презентация", template_id: null, brief: {}, settings: {}, files: [], events: [],
  } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: false } }));
  await page.route("**/api/projects/assistant-unsaved-test/events", (r) => r.fulfill({ status: 500 }));
  await page.route("**/api/brief", (r) => r.fulfill({ json: { understood: [], brief: {}, source: "heuristic" } }));
  let chats = 0;
  await page.route("**/api/chat", (r) => { chats++; return r.fulfill({ status: 500 }); });
  await page.goto("/project?id=assistant-unsaved-test");
  await page.getByTestId("chat-input").fill("Привет");
  await page.getByTestId("chat-send").click();
  await expect(page.getByText("Сообщение не сохранилось", { exact: false })).toBeVisible();
  expect(chats).toBe(0);
});
