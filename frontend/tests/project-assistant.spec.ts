import { expect, test } from "@playwright/test";

test("template-only project opens ONLYOFFICE before any generation", async ({ page }) => {
  await page.route("**/api/projects/template-view-test", (r) => r.fulfill({ json: {
    project_id: "template-view-test", title: "Тест", template_id: "tpl-view", brief: {}, settings: {}, files: [], events: [],
  } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/templates/tpl-view", (r) => r.fulfill({ json: { template_id: "tpl-view", status: "succeeded", name: "Шаблон" } }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: true } }));
  await page.route("**/api/office/templates/tpl-view/config", (r) => r.fulfill({ json: {
    script_url: "/template-sdk.js", config: { editorConfig: { mode: "view" } },
  } }));
  await page.route("**/template-sdk.js", (r) => r.fulfill({ contentType: "application/javascript", body: `
    window.DocsAPI = { DocEditor: function(id, config) {
      if (config.editorConfig.mode !== 'view') throw new Error('Template must be read-only');
      const host = document.getElementById(id); const frame = document.createElement('iframe');
      host.appendChild(frame); setTimeout(() => config.events.onDocumentReady(), 10);
      this.destroyEditor = () => frame.remove();
    }};
  ` }));
  let documents = 0;
  await page.route("**/api/office/documents", (r) => { documents++; return r.fulfill({ status: 500 }); });
  await page.goto("/project?id=template-view-test");
  await expect(page.getByTestId("project-template-office").locator("iframe")).toBeVisible();
  await expect(page.getByText("Открываем исходный PPTX…")).toHaveCount(0);
  await expect(page.getByText("Исходный шаблон · только просмотр.", { exact: false })).toBeVisible();
  expect(documents).toBe(0);
});

test("questions about a generated deck call chat, not office edit, and survive reload", async ({ page }) => {
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
    const event = { event_id: "reply", at: new Date().toISOString(), role: "assistant", kind: "text", text: "Могу объяснить состояние проекта." };
    events.push(event);
    return r.fulfill({ json: { reply: event.text, event, options: ["Что дальше?"], source: "model" } });
  });
  await page.goto("/project?id=assistant-test");
  await expect(page.getByTestId("project-office")).toBeVisible();
  await page.getByTestId("chat-input").fill("Что ты умеешь?");
  await page.getByTestId("chat-send").click();
  await expect(page.getByText("Могу объяснить состояние проекта.")).toBeVisible();
  await page.getByTestId("chat-suggestions").getByRole("button").click();
  await expect(page.getByTestId("chat-input")).toHaveValue("Что дальше?");
  expect(chats).toBe(1);
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
  const doc = { id: "async-doc", revision: 0, active_key: "key", revisions: [] };
  await page.route("**/api/office/documents", (r) => { opens++; return r.fulfill({ json: doc }); });
  await page.route("**/api/office/documents/async-doc", (r) => r.fulfill({ json: doc }));
  await page.route("**/api/office/documents/async-doc/config", (r) => r.fulfill({ json: { script_url: "/async-sdk.js", config: {} } }));
  await page.route("**/async-sdk.js", (r) => r.fulfill({ contentType: "application/javascript", body: `
    window.DocsAPI = { DocEditor: function(id, config) {
      const frame = document.createElement('iframe'); document.getElementById(id).appendChild(frame);
      setTimeout(() => config.events.onDocumentReady(), 0); this.destroyEditor = () => frame.remove();
    }};
  ` }));
  await page.goto("/project?id=async-test");
  await expect(page.getByTestId("project-office").locator("iframe")).toBeVisible();
  await expect(page.getByText("Готовый вариант уже открыт.", { exact: false })).toBeVisible();
  finished = true;
  await expect(page.getByText("Готовый вариант уже открыт.", { exact: false })).toHaveCount(0, { timeout: 20000 });
  await expect(page.getByTestId("project-office").locator("iframe")).toBeVisible();
  expect(opens).toBe(1);
});
