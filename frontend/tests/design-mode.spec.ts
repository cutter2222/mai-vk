import { expect, test, type Page } from "@playwright/test";

// Режим оформления не спрашивается: сборка идёт со смешанным, а сменить режим можно фразой.
async function setup(page: Page) {
  const events: Record<string, unknown>[] = [{
    event_id: "brief", at: new Date().toISOString(), role: "assistant", kind: "brief_card",
    understood: [], missing_purpose: false,
  }];
  const project = {
    project_id: "design-mode-test", title: "Тест", template_id: "tpl-test", package_id: "pkg-test",
    brief: { title: "Пилот", purpose: "product" },
    settings: { variants: ["compact"], design_mode: null as string | null }, files: [], events,
  };
  await page.route("**/api/projects/design-mode-test", (r) => {
    if (r.request().method() === "PATCH") Object.assign(project, r.request().postDataJSON());
    return r.fulfill({ json: project });
  });
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: false } }));
  await page.route("**/api/projects/design-mode-test/events", (r) => {
    const event = { ...r.request().postDataJSON(), event_id: `event-${events.length}`, at: new Date().toISOString() };
    events.push(event);
    return r.fulfill({ json: event });
  });
  const requests: Record<string, unknown>[] = [];
  await page.route("**/api/generations", (r) => {
    requests.push(r.request().postDataJSON());
    return r.fulfill({ status: 503, json: { error: { message: "Тест: запуск остановлен" } } });
  });
  return { project, events, requests };
}

test("без вопроса о режиме: сборка идёт со смешанным", async ({ page }) => {
  const { events, requests } = await setup(page);
  await page.goto("/project?id=design-mode-test");
  await expect(page.getByTestId("brief-card")).toBeVisible();
  await expect(page.getByTestId("chat-suggestions")).toHaveCount(0);
  await page.getByTestId("generate").click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].settings).toMatchObject({ design_mode: "mixed", variants: ["compact"] });
  expect(events.some((e) => typeof e.text === "string" && e.text.startsWith("Как оформить"))).toBe(false);
});

for (const [mode, label] of [
  ["template_only", "По шаблону"],
  ["all_new", "Все слайды новые"],
] as const) {
  test(`режим «${label}» фразой в чате уходит в запрос`, async ({ page }) => {
    const { project, events, requests } = await setup(page);
    await page.route("**/api/chat", (r) => {
      const event = events.find((e) => e.event_id === r.request().postDataJSON().event_id);
      expect(event?.text).toBe(label);
      project.settings.design_mode = mode;
      const reply = { event_id: "reply", at: new Date().toISOString(), role: "assistant", kind: "text", text: `Режим «${label}» сохранён.` };
      events.push(reply);
      return r.fulfill({ json: { reply: reply.text, event: reply, options: [], source: "rules" } });
    });
    await page.goto("/project?id=design-mode-test");
    await page.getByTestId("chat-input").fill(label);
    await page.getByTestId("chat-send").click();
    await expect(page.getByText(`Режим «${label}» сохранён.`, { exact: true })).toBeVisible();
    await page.reload();
    await page.getByTestId("generate").click();
    await expect.poll(() => requests.length).toBe(1);
    expect(requests[0].settings).toMatchObject({ design_mode: mode, variants: ["compact"] });
  });
}
