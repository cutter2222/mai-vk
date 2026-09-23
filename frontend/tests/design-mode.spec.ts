import { expect, test } from "@playwright/test";

for (const [mode, label] of [
  ["template_only", "По шаблону"],
  ["mixed", "Смешанный"],
  ["all_new", "Все слайды новые"],
] as const) {
  test(`chat persists ${mode} and sends it independently of density`, async ({ page }) => {
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
    await page.route("**/api/chat", (r) => {
      const event = events.find((e) => e.event_id === r.request().postDataJSON().event_id);
      expect(event?.text).toBe(label);
      project.settings.design_mode = mode;
      const reply = { event_id: "reply", at: new Date().toISOString(), role: "assistant", kind: "text", text: `Режим «${label}» сохранён.` };
      events.push(reply);
      return r.fulfill({ json: { reply: reply.text, event: reply, options: [], source: "rules" } });
    });
    const requests: Record<string, unknown>[] = [];
    await page.route("**/api/generations", (r) => {
      requests.push(r.request().postDataJSON());
      return r.fulfill({ status: 503, json: { error: { message: "Тест: запуск остановлен" } } });
    });
    await page.goto("/project?id=design-mode-test");
    await expect(page.getByTestId("chat-suggestions").getByRole("button")).toHaveCount(3);
    await page.getByTestId("generate").click();
    await expect(page.getByText("Выберите режим оформления", { exact: true })).toBeVisible();
    expect(requests).toHaveLength(0);
    await page.getByTestId("chat-suggestions").getByRole("button", { name: label, exact: true }).click();
    await page.getByTestId("chat-send").click();
    await expect(page.getByText(`Режим «${label}» сохранён.`, { exact: true })).toBeVisible();
    await expect(page.getByTestId("chat-suggestions")).toHaveCount(0);
    await page.reload();
    await expect(page.getByText(`Режим «${label}» сохранён.`, { exact: true })).toBeVisible();
    await page.getByTestId("generate").click();
    await expect.poll(() => requests.length).toBe(1);
    expect(requests[0].settings).toMatchObject({ design_mode: mode, variants: ["compact"] });
    expect(events.filter((e) => typeof e.text === "string" && e.text.startsWith("Как оформить презентацию?"))).toHaveLength(1);
  });
}