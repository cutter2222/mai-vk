import { expect, test } from "@playwright/test";

// Real API/build with a deterministic SDK: no LLM calls and no changes to the user's PPTX.
test.describe("ONLYOFFICE inside the project", () => {
  test.beforeEach(async ({ page }) => {
    page.setDefaultTimeout(15_000);
    if (process.env.ONLYOFFICE_PROJECT_ID) return;
    // Entirely isolated fixtures: never open or update a user's document.
    await page.route("**/api/projects/office-ui-test", (route) => route.fulfill({ json: {
      project_id: "office-ui-test", title: "Тестовая презентация", job_id: "job_officeuitest", files: [], brief: {}, settings: {},
      events: [
        { event_id: "job", role: "assistant", kind: "job_card", job_id: "job_officeuitest" },
        { event_id: "audit", role: "assistant", kind: "audit_card", job_id: "job_officeuitest" },
        { event_id: "repair", role: "assistant", kind: "edit_card", job_id: "job_officeuitest", edit_job_id: "rep_old" },
      ],
    } }));
    await page.route("**/api/templates", (route) => route.fulfill({ json: [] }));
    await page.route("**/api/generations/job_officeuitest", (route) => route.fulfill({ json: {
      job_id: "job_officeuitest", status: "succeeded", stage: "done", created_at: new Date().toISOString(),
      metrics: { totals: { duration_ms: 1000 } }, execution_mode: { mode: "real", layers: {} },
      variants: ["compact", "balanced"].map((variant_id) => ({ variant_id, status: "ready", revision: 1, slide_count: 1,
        audit: { status: "done" }, artifacts: { pptx: `${variant_id}/r1/deck.pptx` } })),
    } }));
  });

  test("single editor retains its session across panel toggles, downloads versions and safely switches variants", async ({ page }) => {
    const auditRequests: string[] = [];
    page.on("request", (request) => { if (/\/audit(?:[/?]|$)/.test(request.url())) auditRequests.push(request.url()); });
    let configs = 0;
    let active = true;
    let failPoll = false;
    let failConfig = true;
    const openedArtifacts: string[] = [];
    const doc = () => ({ id: "embed-test", source: "test", title: "Test", revision: 3, active_key: active ? "test-key" : null, error: null,
      revisions: [{ revision: 3, sha256: "test", saved_at: 1 }, { revision: 0, sha256: "seed", saved_at: 0 }] });
    await page.route("**/api/office/capabilities", (route) => route.fulfill({ json: { enabled: true } }));
    await page.route("**/api/office/documents", (route) => {
      openedArtifacts.push(route.request().postDataJSON().artifact);
      return route.fulfill({ json: doc() });
    });
    await page.route(/\/api\/generations\/job_[^/?]+$/, async (route) => {
      if (!process.env.ONLYOFFICE_PROJECT_ID) return route.fallback();
      const response = await route.fetch();
      const result = await response.json();
      const first = result.variants[0];
      result.variants = [first, { ...first, variant_id: "balanced", artifacts: { ...first.artifacts, pptx: "balanced/r1/deck.pptx" } }];
      await route.fulfill({ json: result });
    });
    await page.route("**/api/office/documents/embed-test", (route) => failPoll
      ? route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "Тестовая ошибка проверки" } } })
      : route.fulfill({ json: doc() }));
    await page.route("**/api/office/documents/embed-test/config", (route) => {
      if (failConfig) {
        failConfig = false;
        return route.fulfill({ status: 503, json: { error: { code: "unavailable", message: "Тестовая ошибка SDK" } } });
      }
      configs += 1;
      active = true;
      return route.fulfill({ json: { script_url: "/office-test-sdk.js", config: { width: "100%", height: "100%" } } });
    });
    await page.route("**/office-test-sdk.js", (route) => route.fulfill({ contentType: "application/javascript", body: `
      window.DocsAPI = { DocEditor: function(id, config) {
        const host = document.getElementById(id);
        const frame = document.createElement('iframe');
        frame.title = 'ONLYOFFICE test'; frame.style.cssText = 'width:100%;height:100%';
        host.replaceWith(frame);
        setTimeout(() => config.events.onDocumentReady(), 20);
        this.destroyEditor = () => frame.replaceWith(host);
      }};
    ` }));
    let downloaded = "";
    await page.route("**/api/office/documents/embed-test/download/*", (route) => {
      downloaded = route.request().url();
      return route.fulfill({ contentType: "application/vnd.openxmlformats-officedocument.presentationml.presentation", body: "test-pptx" });
    });

    await page.goto(`/project?id=${process.env.ONLYOFFICE_PROJECT_ID ?? "office-ui-test"}`);
    const office = page.getByTestId("project-office");
    await expect(page.getByText("Тестовая ошибка SDK")).toBeVisible();
    await page.getByRole("button", { name: "Повторить загрузку редактора" }).click();
    await expect(office.locator("iframe")).toBeVisible();
    await expect(page.getByText("Загружается редактор…")).toHaveCount(0);
    await expect(page.getByTestId("toggle-editor")).toHaveCount(0);
    expect(configs).toBe(1);
    await expect(page.getByTestId("workspace-switch")).toHaveCount(0);
    await expect(page.getByTestId("audit-card")).toHaveCount(0);
    await expect(page.getByTestId("tab-audit")).toHaveCount(0);
    await expect(page.getByText(/ИИ-исходник/)).toHaveCount(0);
    await page.getByTestId("panel-collapse").click();
    await page.getByTestId("panel-expand").click();
    await page.getByTestId("tab-files").click();
    await expect(page.getByText(/Собрано сервисом/)).toHaveCount(0);
    await page.getByTestId("tab-chat").click();
    await expect(office.locator("iframe")).toBeVisible();
    expect(configs).toBe(1);
    expect(openedArtifacts).toHaveLength(1);
    await page.getByRole("button", { name: "Действия с презентацией" }).click();
    await expect(page.getByRole("combobox", { name: "Вариант презентации", exact: true })).toBeDisabled();
    await page.keyboard.press("Escape");

    await expect(page.locator(".editor-header").getByTestId("template-menu")).toHaveCount(0);
    await expect(page.getByTestId("chat-list").getByTestId("msg-assistant").first()).toContainText("Добавьте шаблон презентации");
    await expect(page.getByTestId("download-menu")).toHaveText("");
    await page.getByTestId("download-menu").click();
    await expect(page.getByRole("menuitem", { name: "PDF", exact: true })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: "HTML", exact: true })).toBeVisible();
    await page.getByTestId("dl-pptx").click();
    await expect.poll(() => downloaded).toContain("/download/3");
    for (const format of ["pdf", "html"]) {
      await page.getByTestId("download-menu").click();
      await page.getByTestId(`dl-${format}`).click();
      await expect.poll(() => downloaded).toContain(`/download/3?format=${format}`);
    }
    await page.getByRole("button", { name: "Действия с презентацией" }).click();
    await page.getByRole("combobox", { name: "Сохранённая версия для скачивания" }).click();
    await page.getByRole("option", { name: "v0 · начальная версия" }).click();
    await page.keyboard.press("Escape");
    await page.getByTestId("download-menu").click();
    await page.getByTestId("dl-pptx").click();
    await expect.poll(() => downloaded).toContain("/download/0");

    failPoll = true;
    await expect(page.getByText("Не удаётся проверить сохранение:", { exact: false })).toBeVisible();
    await expect(page.getByTestId("download-menu")).toBeDisabled();
    failPoll = false;
    active = false;
    await page.getByRole("button", { name: "Действия с презентацией" }).click();
    await page.getByRole("menuitem", { name: "Завершить редактирование" }).click();
    await expect(page.getByText("Сессия закрыта.", { exact: false })).toBeVisible();
    await page.getByRole("combobox", { name: "Вариант презентации", exact: true }).click();
    await page.getByRole("option", { name: "Сбалансированный", exact: true }).click();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Открыть выбранную версию" }).click();
    await expect(office.locator("iframe")).toBeVisible();
    expect(configs).toBe(2);
    expect(openedArtifacts).toHaveLength(2);
    expect(openedArtifacts[1]).toBe("balanced/r1/deck.pptx");
    await page.screenshot({ path: test.info().outputPath("compact-editor-desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 1280, height: 800 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(office.locator(".office-toolbar")).toHaveCount(0);
    await expect(office.getByText("Презентация", { exact: true })).toHaveCount(0);
    await expect(page.getByText("На сервере · v3", { exact: true })).not.toBeVisible();
    await expect(page.locator(".editor-header").getByTestId("download-menu")).toBeVisible();
    await expect(page.locator(".editor-header").getByRole("button", { name: "Действия с презентацией" })).toBeVisible();
    const workspaceBox = await page.getByTestId("office-workspace").boundingBox();
    const canvasBox = await office.locator(".office-canvas").boundingBox();
    expect(workspaceBox).not.toBeNull();
    expect(canvasBox).not.toBeNull();
    expect(canvasBox!.y).toBe(workspaceBox!.y);
    expect(auditRequests).toEqual([]);
    await page.screenshot({ path: test.info().outputPath("compact-editor-laptop.png"), fullPage: true });
  });

  test("chat waits for final save, edits the same PPTX and reopens it", async ({ page }) => {
    let active = true;
    let configs = 0;
    let edits = 0;
    let revision = 3;
    const doc = () => ({ id: "chat-test", source: "test", title: "Test", revision,
      active_key: active ? "key" : null, error: null, revisions: [{ revision, sha256: "test", saved_at: 1 }] });
    await page.route("**/api/office/capabilities", (route) => route.fulfill({ json: { enabled: true } }));
    await page.route("**/api/office/documents", (route) => route.fulfill({ json: doc() }));
    await page.route("**/api/office/documents/chat-test", (route) => route.fulfill({ json: doc() }));
    await page.route("**/api/office/documents/chat-test/config", (route) => {
      configs++; active = true;
      return route.fulfill({ json: { script_url: "/chat-test-sdk.js", config: {} } });
    });
    await page.route("**/chat-test-sdk.js", (route) => route.fulfill({ contentType: "application/javascript", body: `
      window.DocsAPI = { DocEditor: function(id, config) {
        const frame = document.createElement('iframe');
        document.getElementById(id).appendChild(frame);
        setTimeout(() => config.events.onDocumentReady(), 0);
        this.requestClose = () => config.events.onRequestClose();
        this.destroyEditor = () => { frame.remove(); fetch('/chat-test-closed', {method:'POST'}); };
      }};` }));
    await page.route("**/chat-test-closed", async (route) => {
      // The app must not call /edit before this final-save acknowledgement.
      await new Promise((resolve) => setTimeout(resolve, 1200));
      expect(edits).toBe(0);
      active = false; revision = 4;
      await route.fulfill({ body: "ok" });
    });
    await page.route("**/api/office/documents/chat-test/edit", (route) => {
      expect(active).toBe(false);
      expect(route.request().postDataJSON()).toEqual({ revision: 4, instruction: "Измени заголовок на Новый" });
      edits++; revision = 5;
      return route.fulfill({ json: { document: doc(), changed: true, message: "Заголовок изменён" } });
    });
    await page.route("**/api/projects/office-ui-test/events", (route) => route.fulfill({ json: {
      ...route.request().postDataJSON(), event_id: `event-${Date.now()}`,
    } }));
    await page.goto("/project?id=office-ui-test");
    await expect(page.getByTestId("office-workspace").locator("iframe")).toBeVisible();
    await page.getByTestId("chat-input").fill("/edit Измени заголовок на Новый");
    await page.getByTestId("chat-input").press("Enter");
    await expect.poll(() => edits).toBe(1);
    await expect.poll(() => configs).toBe(2);
    await expect(page.getByTestId("office-workspace").locator("iframe")).toBeVisible();
    await expect(page.getByText("Правка сохранена в этом PPTX", { exact: false })).toBeVisible();
  });

  test("offers retry when the office document cannot be opened", async ({ page }) => {
    await page.route("**/api/office/capabilities", (route) => route.fulfill({ json: { enabled: true } }));
    await page.route("**/api/office/documents", (route) => route.fulfill({ status: 503, json: { error: { code: "office_unavailable", message: "Тестовая ошибка открытия" } } }));
    await page.goto(`/project?id=${process.env.ONLYOFFICE_PROJECT_ID ?? "office-ui-test"}`);
    await expect(page.getByText("Тестовая ошибка открытия")).toBeVisible();
    await expect(page.getByRole("button", { name: "Повторить открытие" })).toBeEnabled();
    await expect(page.getByTestId("workspace-switch")).toHaveCount(0);
    await expect(page.getByTestId("project-office")).toBeVisible();
  });

  test("does not fall back to the source editor when ONLYOFFICE is unavailable", async ({ page }) => {
    await page.route("**/api/office/capabilities", (route) => route.fulfill({ json: { enabled: false } }));
    await page.goto(`/project?id=${process.env.ONLYOFFICE_PROJECT_ID ?? "office-ui-test"}`);
    await expect(page.getByText("Редактор недоступен", { exact: true })).toBeVisible();
    await expect(page.getByTestId("workspace-switch")).toHaveCount(0);
    await expect(page.getByTestId("toggle-editor")).toHaveCount(0);
    await expect(page.getByTestId("audit-card")).toHaveCount(0);
  });

  test("live editor opens on the project page without an intermediate slide canvas", async ({ page }) => {
    test.skip(process.env.ONLYOFFICE_PROJECT_LIVE !== "1" || !process.env.ONLYOFFICE_PROJECT_ID, "Opt-in real Document Server");
    test.setTimeout(240_000);
    await page.goto(`/project?id=${process.env.ONLYOFFICE_PROJECT_ID}`);
    await expect(page.getByTestId("project-office").locator("iframe")).toBeVisible({ timeout: 120_000 });
    await expect(page.getByText("Загружается редактор…")).toHaveCount(0, { timeout: 180_000 });
    await expect(page.getByText("Ошибка ONLYOFFICE.", { exact: false })).toHaveCount(0);
    await expect(page.getByTestId("toggle-editor")).toHaveCount(0);
    const iframe = page.getByTestId("project-office").locator("iframe");
    await expect(page.locator(".editor-header").getByTestId("template-menu")).toHaveCount(0);
    await expect(page.getByTestId("template-start")).toHaveCount(1);
    await expect(page.getByTestId("download-menu")).toHaveText("");
    await page.getByTestId("download-menu").click();
    for (const format of ["pptx", "pdf", "html"]) {
      await expect(page.getByTestId(`dl-${format}`)).toBeVisible();
    }
    await page.keyboard.press("Escape");
    const frameUrl = await iframe.getAttribute("src");
    await page.getByTestId("panel-collapse").click();
    await page.getByTestId("panel-expand").click();
    await expect(iframe).toBeVisible();
    await expect(iframe).toHaveAttribute("src", frameUrl!);
    await page.screenshot({ path: test.info().outputPath("project-onlyoffice.png"), fullPage: true });
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.getByTestId("panel-collapse").click();
    await expect(iframe).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath("project-onlyoffice-laptop.png"), fullPage: true });
    await page.getByRole("button", { name: "Действия с презентацией" }).click();
    await page.getByRole("menuitem", { name: "Завершить редактирование" }).click();
    await expect(iframe).toHaveCount(0);
    // The user's document may remain open in another tab. Do not terminate that session.
    await expect(page.getByRole("alert").filter({ hasText: /Сессия закрыта\.|Ожидаем завершения сессии и сохранения/ })).toBeVisible();
  });
});