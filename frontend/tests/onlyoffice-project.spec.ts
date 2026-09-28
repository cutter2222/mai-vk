import { expect, test, type Page } from "@playwright/test";

import { mockRouter } from "./helpers";

// Isolated API and SDK fixtures: never edit a user's deck.
test.beforeEach(async ({ page }) => {
  // Existing preview/fullscreen regressions explicitly enter the secondary saved view.
  await page.addInitScript(() => {
    if (location.pathname === "/project" && !location.search.includes("officeView=")) {
      history.replaceState(null, "", `${location.href}&officeView=preview`);
    }
  });
});

test("slides open the editor inline and wait for server save before preview", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/project?id=office-ui-test&officeView=editor");
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
  await expect(page.getByTestId("office-preview")).toBeEnabled();
  await expect(page.getByTestId("slide-counter")).toHaveCount(0);
  await expect(page.getByText(/Сохранённая версия · v/)).toHaveCount(0);
  // Переключатель вариантов виден и в редакторе: варианты собираются по очереди.
  await expect(page.getByTestId("office-variants")).toBeVisible();
  const pane = await page.getByTestId("preview-pane").boundingBox();
  const canvas = await page.locator(".office-canvas").boundingBox();
  expect(pane).not.toBeNull();
  expect(canvas).not.toBeNull();
  expect(Math.abs(canvas!.y - pane!.y)).toBeLessThanOrEqual(1);
  await page.getByTestId("panel-collapse").click();
  await page.getByTestId("panel-expand").click();
  expect(state.configs).toBe(1);
  await page.getByTestId("office-preview").click();
  await expect(page.locator("iframe")).toHaveCount(0);
  await expect(page.getByTestId("slide-counter")).toHaveCount(0);
  await expect(page.getByText("Ожидаем завершения сессии", { exact: false })).toBeVisible();
  state.revision = 4;
  state.active = false;
  await expect(page.getByText("Превью · v4", { exact: true })).toBeVisible();
  await expect(page.getByText(/Сохранённая версия · v/)).toHaveCount(0);
  await expect(page.getByTestId("office-variants")).toBeVisible();
  await expect(page).toHaveURL(/\/project\?/);
  await page.getByTestId("edit-slides").click();
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
  expect(state.configs).toBe(2);
  expect(state.opened).toEqual(["compact/r1/deck.pptx"]);
});

test("logo request with an image replaces the template logo on every slide in one edit", async ({ page }) => {
  const state = await setup(page);
  const patches: unknown[] = [];
  await page.route("**/api/projects/office-ui-test/files", (r) => r.fulfill({ json: [
    { file_id: "file-logo", name: "logo.png", size_bytes: 68, kind: "material", check: { format: "png", status: "ok" } },
  ] }));
  await page.route("**/api/projects/office-ui-test/files/file-logo", (r) => {
    patches.push(r.request().postDataJSON());
    return r.fulfill({ json: { file_id: "file-logo", name: "logo.png", size_bytes: 68, kind: "other", check: { format: "png", status: "ok" } } });
  });
  await page.goto("/project?id=office-ui-test&officeView=editor");
  await expect(page.getByTestId("office-preview")).toBeEnabled();
  const chooser = page.waitForEvent("filechooser");
  await page.getByTestId("chat-attach").click();
  await (await chooser).setFiles({ name: "logo.png", mimeType: "image/png", buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC", "base64") });
  await page.getByTestId("chat-input").fill("Замени логотип на наш");
  await page.getByTestId("chat-send").click();
  await expect(page.locator("iframe")).toHaveCount(0);
  state.active = false;
  await expect(page.getByText(/Логотип шаблона заменён на всех слайдах/)).toBeVisible();
  // Одна правка документа с картинкой из файлов проекта; картинка — не материал для содержания.
  expect(state.edits).toBe(1);
  expect(state.lastEdit?.logo).toEqual({ action: "replace", file_id: "file-logo" });
  expect(patches).toContainEqual({ kind: "other" });
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
});

test("logo request without an image asks for it, and removal needs none", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/project?id=office-ui-test&officeView=editor");
  await expect(page.getByTestId("office-preview")).toBeEnabled();
  await page.getByTestId("chat-input").fill("Поменяй логотип");
  await page.getByTestId("chat-send").click();
  await expect(page.getByText(/Прикрепите картинку нового логотипа/)).toBeVisible();
  expect(state.edits).toBe(0);
  await page.getByTestId("chat-input").fill("Убери лого со всех слайдов");
  await page.getByTestId("chat-send").click();
  await expect(page.locator("iframe")).toHaveCount(0);
  state.active = false;
  await expect(page.getByText(/Логотип шаблона/)).toBeVisible();
  expect(state.lastEdit?.logo).toEqual({ action: "remove" });
});

test("inline AI saves the active editor, edits the same PPTX and reopens inline", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/project?id=office-ui-test&officeView=editor");
  await expect(page.getByTestId("office-preview")).toBeEnabled();
  await page.getByTestId("chat-input").fill("/edit Измени заголовок");
  await page.getByTestId("chat-send").click();
  await expect(page.locator("iframe")).toHaveCount(0);
  expect(state.edits).toBe(0);
  state.active = false;
  await expect(page.getByText(/Заголовок изменён/)).toBeVisible();
  await expect(page.getByTestId("preview-pane").locator("iframe")).toBeVisible();
  await expect(page).toHaveURL(/\/project\?/);
  expect(state.edits).toBe(1);
  expect(state.configs).toBe(2);
});

test("pages load independently and a failed page can be retried", async ({ page }) => {
  const state = await setup(page);
  await page.route(/\/preview-test\/preview\/3$/, (r) => r.fulfill({ json: {
    revision: 3, slides: ["slide-01.png", "slide-02.png", "slide-03.png"], ratio: 16 / 9,
  } }));
  let fail = true;
  await page.route(/\/slide-0[23]\.png(?:\?.*)?$/, (r) => fail
    ? r.fulfill({ status: 503 })
    : r.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC", "base64") }));
  await page.goto("/project?id=office-ui-test");
  const image = page.locator(".preview-stage img");
  await expect(image).toBeVisible();
  await expect(image).toHaveAttribute("src", /slide-01.png$/);
  await expect(page.getByTestId("slide-counter")).toHaveText("Слайд 1 из 3");
  await expect(page.locator(".thumb-image img").first()).toHaveAttribute("loading", "lazy");
  await page.getByTestId("thumb-1").click();
  const retry = page.locator(".preview-stage").getByRole("button", { name: "Повторить загрузку слайда" });
  await expect(retry).toBeVisible();
  fail = false;
  await retry.click();
  await expect(image).toBeVisible();
  await expect(image).toHaveAttribute("src", /slide-02.png\?retry=1$/);
  const thumbnail = page.getByTestId("thumb-1").locator("img");
  await expect(thumbnail).toHaveAttribute("src", /slide-02.png\?retry=1$/);
  await expect(thumbnail).toBeVisible();
  await expect(page.getByTestId("thumb-1").getByText("Откройте слайд, чтобы повторить")).toHaveCount(0);
  await page.getByTestId("thumb-0").click();
  await expect(image).toBeVisible();
  expect(state.configs).toBe(0);
});

async function setup(page: Page) {
  await mockRouter(page);
  const state = { active: false, revision: 3, error: null as string | null, failPoll: false, failPreview: false, configs: 0, edits: 0, opened: [] as string[], lastEdit: null as Record<string, unknown> | null };
  const doc = () => ({ id: "preview-test", source: "test", title: "Test", revision: state.revision,
    active_key: state.active ? "key" : null, error: state.error, revisions: [{ revision: state.revision, sha256: "test", saved_at: 1 }] });
  await page.route("**/api/projects/office-ui-test", (r) => r.fulfill({ json: {
    project_id: "office-ui-test", title: "Тестовая презентация", job_id: "job_officeuitest", files: [], brief: {}, settings: {}, events: [],
  } }));
  await page.route("**/api/projects/office-ui-test/events", (r) => r.fulfill({ json: { ...r.request().postDataJSON(), event_id: String(Date.now()), at: new Date().toISOString() } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/generations/job_officeuitest", (r) => r.fulfill({ json: {
    job_id: "job_officeuitest", status: "succeeded", stage: "done", metrics: { totals: { duration_ms: 1000 } }, execution_mode: { mode: "real", layers: {} },
    variants: ["compact", "balanced"].map((variant_id) => ({ variant_id, status: "ready", revision: 1, artifacts: { pptx: `${variant_id}/r1/deck.pptx` } })),
  } }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: true } }));
  await page.route("**/api/office/documents", (r) => { state.opened.push(r.request().postDataJSON().artifact); return r.fulfill({ json: doc() }); });
  await page.route("**/api/office/documents/preview-test", (r) => state.failPoll
    ? r.fulfill({ status: 503, json: { error: { message: "Сервер недоступен" } } }) : r.fulfill({ json: doc() }));
  await page.route(/\/api\/office\/documents\/preview-test\/objects\/\d+$/, (r) => r.fulfill({ json: {
    revision: Number(r.request().url().split("/").pop()), objects: [{ slide: 1, shape_id: "2", label: "Заголовок", kind: "sp", bbox: { x: 0.1, y: 0.1, width: 0.6, height: 0.2 }, z: 1, hollow: false }],
  } }));
  await page.route(/\/api\/office\/documents\/preview-test\/preview\/\d+$/, (r) => state.failPreview
    ? r.fulfill({ status: 503, json: { error: { message: "Ошибка превью" } } })
    : r.fulfill({ json: { revision: Number(r.request().url().split("/").pop()), slides: ["slide-01.png"], ratio: 16 / 9 } }));
  await page.route("**/slide-01.png", (r) => r.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC", "base64") }));
  await page.route("**/api/office/documents/preview-test/config", (r) => {
    state.configs++; state.active = true;
    return r.fulfill({ json: { script_url: "/office-test-sdk.js", config: {} } });
  });
  await page.route("**/office-test-sdk.js", (r) => r.fulfill({ contentType: "application/javascript", body: `
    window.DocsAPI = { DocEditor: function(id, config) {
      const frame = document.createElement('iframe'); document.getElementById(id).appendChild(frame);
      const callbacks = {};
      const api = {
        asc_Save: () => { window.testSaveCalls = (window.testSaveCalls || 0) + 1; return !!window.testSavePending; },
        isDocumentModified: () => !!window.testSavePending,
        asc_isDocumentCanSave: () => !!window.testSavePending,
        asc_registerCallback: (name, fn) => { callbacks[name] = fn; },
        asc_unregisterCallback: name => { delete callbacks[name]; },
      };
      frame.contentWindow.PE = { getController: () => ({ getApi: () => api }) };
      frame.contentWindow.Asc = { c_oAscAsyncAction: { Save: 1 } };
      window.testSaveEnd = () => { window.testSavePending = false; callbacks.asc_onEndAction?.(0, 1); };
      window.testSaveError = () => callbacks.asc_onError?.();
      setTimeout(() => config.events.onDocumentReady(), 0);
      this.requestClose = () => config.events.onRequestClose();
      this.destroyEditor = () => frame.remove();
      window.testOfficeDirty = value => {
        window.testSavePending = value;
        config.events.onDocumentStateChange({data: value});
        if (!value) callbacks.asc_onEndAction?.(0, 1);
      };
    }};
  ` }));
  await page.route("**/api/office/documents/preview-test/edit", (r) => {
    state.edits++;
    expect(state.active).toBe(false);
    const body = r.request().postDataJSON();
    state.lastEdit = body;
    expect(body.revision).toBe(state.revision);
    state.revision++;
    const message = body.logo ? "Логотип шаблона заменён на всех слайдах (9 мест в макетах)." : "Заголовок изменён";
    return r.fulfill({ json: { document: doc(), changed: true, message } });
  });
  return state;
}

test("preview opens no SDK, survives panel toggles, downloads saved revision", async ({ page }) => {
  const state = await setup(page);
  const downloads: string[] = [];
  await page.route("**/api/office/documents/preview-test/download/**", (r) => {
    downloads.push(r.request().url()); return r.fulfill({ body: "saved-file" });
  });
  await page.goto("/project?id=office-ui-test");
  await expect(page.getByText("Превью · v3", { exact: true })).toBeVisible();
  await expect(page.locator("iframe")).toHaveCount(0);
  await page.getByTestId("panel-collapse").click();
  await page.getByTestId("panel-expand").click();
  for (const format of ["pptx", "pdf", "html"]) {
    await page.getByTestId("download-menu").click();
    await page.getByTestId(`dl-${format}`).click();
    await expect.poll(() => downloads.some((url) => url.endsWith(`/download/3${format === "pptx" ? "" : `?format=${format}`}`))).toBe(true);
  }
  expect(state.configs).toBe(0);
  expect(state.opened).toEqual(["compact/r1/deck.pptx"]);
});

test("multi-slide repair in saved preview uses each new document revision", async ({ page }) => {
  const state = await setup(page);
  let repaired = false;
  const applied: Array<{ revision: number; slide: number }> = [];
  await page.route("**/api/generations/job_officeuitest", (r) => r.fulfill({ json: {
    job_id: "job_officeuitest", status: "succeeded", stage: "done", metrics: { totals: { duration_ms: 1000 } },
    variants: [{ variant_id: "compact", status: "ready", revision: repaired ? 2 : 1, artifacts: { pptx: `compact/r${repaired ? 2 : 1}/deck.pptx` } }],
    repairs: repaired ? [{ repair_job_id: "repair_many", variant_id: "compact", result: "applied", base_revision: 1, new_revision: 2, changed_slide_ids: ["s1", "s2"] }] : [],
  } }));
  await page.route("**/api/chat/route", (r) => r.fulfill({ json: { kind: "run", steps: [{ action: "repair", slides: [] }], text: "", options: [], source: "rules" } }));
  await page.route("**/variants/compact/audit?revision=1", (r) => r.fulfill({ json: { issues: [{ issue_id: "issue_1", slide_index: 0 }] } }));
  await page.route("**/variants/compact/repairs", (r) => { repaired = true; return r.fulfill({ json: { repair_job_id: "repair_many" } }); });
  await page.route("**/api/jobs/repair_many", (r) => r.fulfill({ json: { status: "succeeded" } }));
  await page.route("**/artifacts/compact/r2/plan.json", (r) => r.fulfill({ json: { slides: [{ slide_id: "s1", order: 0 }, { slide_id: "s2", order: 1 }] } }));
  await page.route("**/api/office/documents/preview-test/apply-slide", (r) => {
    const body = r.request().postDataJSON();
    applied.push(body);
    if (body.revision !== state.revision) return r.fulfill({ status: 409, json: { error: { message: "stale revision" } } });
    state.revision++;
    return r.fulfill({ json: { document: { id: "preview-test", revision: state.revision, active_key: null }, changed: true, message: "Перенесён" } });
  });
  await page.goto("/project?id=office-ui-test");
  await expect(page.getByText("Превью · v3", { exact: true })).toBeVisible();
  await page.getByTestId("chat-input").fill("исправь замечания");
  await page.getByTestId("chat-send").click();
  await expect(page.getByText("Превью · v5", { exact: true })).toBeVisible();
  expect(applied.map(({ revision, slide }) => [revision, slide])).toEqual([[3, 1], [4, 2]]);
  expect(state.configs).toBe(0);
});

test("immediate close flushes even with a clean parent flag and waits for SDK acknowledgement", async ({ page }) => {
  await setup(page);
  await page.goto("/project?id=office-ui-test");
  await page.getByTestId("open-office").click();
  await expect(page.getByRole("button", { name: "Завершить и сохранить" })).toBeEnabled();
  await page.evaluate("window.testSavePending = true");
  await page.getByRole("button", { name: "Завершить и сохранить" }).click();
  await expect.poll(() => page.evaluate("window.testSaveCalls")).toBe(1);
  await expect(page.locator("iframe")).toHaveCount(1);
  await page.evaluate("window.testSaveEnd()");
  await expect(page.locator("iframe")).toHaveCount(0);
});

test("failed SDK save keeps the frame alive and allows retry", async ({ page }) => {
  await setup(page);
  await page.goto("/project?id=office-ui-test");
  await page.getByTestId("open-office").click();
  const finish = page.getByRole("button", { name: "Завершить и сохранить" });
  await expect(finish).toBeEnabled();
  await page.evaluate("window.testSavePending = true");
  await finish.click();
  await expect.poll(() => page.evaluate("window.testSaveCalls")).toBe(1);
  await page.evaluate("window.testSaveError()");
  await expect(page.getByText("ONLYOFFICE не сохранил правки.", { exact: false })).toBeVisible();
  await expect(page.locator("iframe")).toHaveCount(1);
  await finish.click();
  await expect.poll(() => page.evaluate("window.testSaveCalls")).toBe(2);
  await page.evaluate("window.testSaveEnd()");
  await expect(page.locator("iframe")).toHaveCount(0);
});

test("close debounces clean state and flushes edits arriving during save", async ({ page }) => {
  await setup(page);
  await page.goto("/project?id=office-ui-test");
  await page.getByTestId("open-office").click();
  const finish = page.getByRole("button", { name: "Завершить и сохранить" });
  await expect(finish).toBeEnabled();
  await page.clock.install();
  await page.evaluate("window.testSavePending = true");
  await finish.click();
  await expect.poll(() => page.evaluate("window.testSaveCalls")).toBe(1);
  // A second click must not start a parallel flush.
  await finish.dispatchEvent("click");
  await expect.poll(() => page.evaluate("window.testSaveCalls")).toBe(1);
  await page.evaluate("window.testSaveEnd()");
  await page.clock.runFor(100);
  await expect(page.locator("iframe")).toHaveCount(1);
  await page.evaluate("window.testSavePending = true");
  await page.clock.runFor(300);
  await expect.poll(() => page.evaluate("window.testSaveCalls")).toBe(2);
  await page.clock.runFor(500);
  await expect(page.locator("iframe")).toHaveCount(1);
  await page.evaluate("window.testSaveEnd()");
  await page.clock.runFor(200);
  await expect(page.locator("iframe")).toHaveCount(1);
  await page.clock.runFor(300);
  await expect(page.locator("iframe")).toHaveCount(0);
});

test("SDK state read failure leaves editor open and allows a retry", async ({ page }) => {
  await setup(page);
  await page.goto("/project?id=office-ui-test");
  await page.getByTestId("open-office").click();
  const finish = page.getByRole("button", { name: "Завершить и сохранить" });
  await expect(finish).toBeEnabled();
  await page.evaluate(`(() => {
    const api = document.querySelector('iframe').contentWindow.PE.getController().getApi();
    const original = api.isDocumentModified;
    api.isDocumentModified = () => { throw new Error('SDK disconnected'); };
    window.restoreSaveState = () => { api.isDocumentModified = original; };
  })()`);
  await finish.click();
  await expect(page.getByText("Не удалось запустить сохранение ONLYOFFICE.", { exact: false })).toBeVisible();
  await expect(page.locator("iframe")).toHaveCount(1);
  await page.evaluate("window.restoreSaveState()");
  await finish.click();
  await expect(page.locator("iframe")).toHaveCount(0);
});

test("late edits after a no-op save are flushed before close", async ({ page }) => {
  await setup(page);
  await page.goto("/project?id=office-ui-test");
  await page.getByTestId("open-office").click();
  const finish = page.getByRole("button", { name: "Завершить и сохранить" });
  await expect(finish).toBeEnabled();
  await page.clock.install();
  await finish.click();
  await expect.poll(() => page.evaluate("window.testSaveCalls")).toBe(1);
  await page.evaluate("window.testSavePending = true");
  await page.clock.runFor(400);
  await expect.poll(() => page.evaluate("window.testSaveCalls")).toBe(2);
  await expect(page.locator("iframe")).toHaveCount(1);
  await page.evaluate("window.testSaveEnd()");
  await page.clock.runFor(400);
  await expect(page.locator("iframe")).toHaveCount(0);
});

test("callback registration failure clears save wait and permits retry", async ({ page }) => {
  await setup(page);
  await page.goto("/project?id=office-ui-test");
  await page.getByTestId("open-office").click();
  const finish = page.getByRole("button", { name: "Завершить и сохранить" });
  await expect(finish).toBeEnabled();
  await page.evaluate(`(() => {
    const api = document.querySelector('iframe').contentWindow.PE.getController().getApi();
    const original = api.asc_registerCallback;
    api.asc_registerCallback = () => { throw new Error('SDK unavailable'); };
    window.restoreSaveCallbacks = () => { api.asc_registerCallback = original; };
  })()`);
  await finish.click();
  await expect(page.getByText("Не удалось запустить сохранение ONLYOFFICE.", { exact: false })).toBeVisible();
  await expect(page.locator("iframe")).toHaveCount(1);
  await page.evaluate("window.restoreSaveCallbacks()");
  await finish.click();
  await expect(page.locator("iframe")).toHaveCount(0);
});

test("save timeout and unavailable adapter never destroy the iframe", async ({ page }) => {
  await setup(page);
  await page.goto("/project?id=office-ui-test");
  await page.getByTestId("open-office").click();
  const finish = page.getByRole("button", { name: "Завершить и сохранить" });
  await expect(finish).toBeEnabled();
  await page.clock.install();
  await page.evaluate("window.testSavePending = true");
  await finish.click();
  await page.clock.fastForward(31000);
  await expect(page.getByText("Сохранение не подтверждено.", { exact: false })).toBeVisible();
  await expect(page.locator("iframe")).toHaveCount(1);
  await page.evaluate("delete document.querySelector('iframe').contentWindow.PE");
  await finish.click();
  await expect(page.getByText("Не удалось запустить сохранение ONLYOFFICE.", { exact: false })).toBeVisible();
  await expect(page.locator("iframe")).toHaveCount(1);
});

test("manual page waits for callback and poll recovery, then returns to the same variant", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/project?id=office-ui-test");
  await expect(page.getByText("Превью · v3", { exact: true })).toBeVisible();
  // Выбор варианта сразу открывает его PPTX.
  await page.getByTestId("office-variant-balanced").click();
  await expect.poll(() => state.opened.at(-1)).toBe("balanced/r1/deck.pptx");
  await page.getByTestId("open-office").click();
  await expect(page).toHaveURL(/\/office\?/);
  await expect(page.getByTestId("office-workspace").locator("iframe")).toBeVisible();
  const header = page.locator("header");
  await expect(header).toHaveCount(1);
  await expect(header.getByRole("img", { name: "Дизайнер презентаций", exact: true })).toBeVisible();
  await expect(header.getByRole("button")).toHaveCount(1);
  await expect(header.getByRole("button", { name: "Завершить и сохранить" })).toBeEnabled();
  await expect(page.getByTestId("nav-home")).toHaveCount(0);
  await expect(page.getByTestId("nav-templates")).toHaveCount(0);
  await expect(page.getByText("Ручное редактирование · ONLYOFFICE", { exact: true })).toHaveCount(0);
  await expect(header).toHaveCSS("height", "60px");
  expect((await header.boundingBox())?.y).toBe(0);
  await page.evaluate(() => (window as unknown as { testOfficeDirty: (dirty: boolean) => void }).testOfficeDirty(true));
  await page.getByRole("button", { name: "Завершить и сохранить" }).click();
  await expect(page.locator(".office-canvas")).toHaveAttribute("inert", "");
  await expect(page.locator("iframe")).toBeVisible();
  await page.evaluate(() => (window as unknown as { testOfficeDirty: (dirty: boolean) => void }).testOfficeDirty(false));
  // A clean SDK flag is not a storage acknowledgement.
  await expect(page.locator("iframe")).toHaveCount(0);
  await expect(page.getByText("Ожидаем завершения сессии", { exact: false })).toBeVisible();
  await expect(page).toHaveURL(/\/office\?/);
  await expect(page.getByText("Сохранено", { exact: true })).toHaveCount(0);
  state.revision = 4; // force-save still does not release the active key
  await expect(page).toHaveURL(/\/office\?/);
  state.error = "Ошибка сохранения";
  await expect(page.getByText("Ошибка сохранения", { exact: true })).toBeVisible();
  state.failPoll = true;
  await expect(page.getByText("Не удаётся проверить сохранение:", { exact: false })).toBeVisible();
  state.active = false; state.error = null;
  await expect(page).toHaveURL(/\/office\?/);
  await expect(page.getByText("Сохранено", { exact: true })).toHaveCount(0);
  state.failPoll = false;
  await expect(page).toHaveURL(/\/project\?id=office-ui-test&officeJob=job_officeuitest&officeArtifact=balanced%2Fr1%2Fdeck.pptx&officeView=preview$/);
  const saved = page.getByText("Сохранено", { exact: true });
  await expect(saved).toHaveCount(1);
  await expect(saved).toBeVisible();
  expect((await saved.boundingBox())!.y).toBeLessThan(150);
  await expect(page.getByText("Сессия закрыта.", { exact: false })).toHaveCount(0);
  await expect(page.getByTestId("chat-input")).toBeVisible();
  await expect(page.getByRole("link", { name: "К списку презентаций" })).toBeVisible();
  await expect(page.getByText("Превью · v4", { exact: true })).toBeVisible();
  await expect(page.locator(".preview-stage img")).toHaveAttribute("src", /\/preview\/4\/slide-01.png$/);
  expect(state.opened.at(-1)).toBe("balanced/r1/deck.pptx");
  expect(state.configs).toBe(1);
  await page.mouse.move(0, 800);
  await expect(saved).toHaveCount(0);
  await page.reload();
  await expect(page.getByText("Превью · v4", { exact: true })).toBeVisible();
  await expect(saved).toHaveCount(0);
});

test("manual save returns to AI editor even when the saved revision is unchanged", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/project?id=office-ui-test");
  await expect(page.getByText("Превью · v3", { exact: true })).toBeVisible();
  await expect(page.getByText("Сохранено", { exact: true })).toHaveCount(0);
  await page.getByTestId("open-office").click();
  await page.getByRole("button", { name: "Завершить и сохранить" }).click();
  await expect(page.getByText("Ожидаем завершения сессии", { exact: false })).toBeVisible();
  state.error = "Ошибка сохранения";
  state.active = false;
  await expect(page.getByText("Ошибка сохранения", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/office\?/);
  await expect(page.getByText("Сохранено", { exact: true })).toHaveCount(0);
  state.error = null;
  await expect(page).toHaveURL(/\/project\?/);
  await expect(page.getByText("Сохранено", { exact: true })).toBeVisible();
  await expect(page.getByText("Превью · v3", { exact: true })).toBeVisible();
  expect(state.opened.at(-1)).toBe("compact/r1/deck.pptx");
  expect(state.configs).toBe(1);
});

test("new revision replaces canvas and thumbnails after a retry without reload", async ({ page }) => {
  const state = await setup(page);
  await page.route(/\/preview-test\/preview\/4$/, (r) => state.failPreview
    ? r.fulfill({ status: 503, json: { error: { message: "Ошибка превью" } } })
    : r.fulfill({ json: { revision: 4, slides: ["slide-01.png", "slide-02.png"], ratio: 16 / 9 } }));
  await page.route("**/slide-02.png", (r) => r.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC", "base64") }));
  await page.goto("/project?id=office-ui-test");
  const canvas = page.locator(".preview-stage img");
  await expect(canvas).toBeVisible();
  await expect(canvas).toHaveAttribute("src", /\/preview\/3\/slide-01.png$/);
  state.failPreview = true;
  state.revision = 4;
  await expect(page.getByText("Обновляем превью v4; пока показана v3.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Повторить превью" })).toBeVisible();
  await expect(canvas).toHaveAttribute("src", /\/preview\/3\/slide-01.png$/);
  await expect(page.getByText("Превью · v4", { exact: true })).toHaveCount(0);
  state.failPreview = false;
  await page.getByRole("button", { name: "Повторить превью" }).click();
  await expect(page.getByText("Превью · v4", { exact: true })).toBeVisible();
  await expect(page.getByTestId("slide-counter")).toHaveText("Слайд 1 из 2");
  await expect(page.locator(".thumb-image img")).toHaveCount(2);
  for (let index = 0; index < 2; index++) {
    const thumb = page.getByTestId(`thumb-${index}`);
    const path = `/api/office/documents/preview-test/preview/4/slide-0${index + 1}.png`;
    await expect(thumb.locator("img")).toHaveAttribute("src", path);
    await thumb.click();
    await expect(canvas).toHaveAttribute("src", path);
    await expect(canvas).toBeVisible();
  }
  await expect(page.locator('img[src*="/preview/3/"]')).toHaveCount(0);
  expect(state.configs).toBe(0);
  expect(state.opened).toHaveLength(1);
});

test("a delayed pre-close response cannot acknowledge saving", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/project?id=office-ui-test");
  await expect(page.getByText("Превью · v3", { exact: true })).toBeVisible();
  await page.getByTestId("open-office").click();
  const finish = page.getByRole("button", { name: "Завершить и сохранить" });
  await expect(finish).toBeEnabled();
  let release!: () => void;
  let pending = false;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/office/documents/preview-test", async (route) => {
    if (pending) return route.fallback();
    pending = true;
    await gate;
    return route.fulfill({ json: { id: "preview-test", revision: 3, active_key: null, error: null, revisions: [] } });
  });
  await expect.poll(() => pending).toBe(true);
  await finish.click();
  await expect(page.locator("iframe")).toHaveCount(0);
  release();
  await expect(page.getByText("Ожидаем завершения сессии", { exact: false })).toBeVisible();
  await expect(page).toHaveURL(/\/office\?/);
  await expect(page.getByText("Сохранено", { exact: true })).toHaveCount(0);
  state.revision = 4;
  state.active = false;
  await expect(page).toHaveURL(/\/project\?/);
  await expect(page.getByText("Превью · v4", { exact: true })).toBeVisible();
});

test("AI is blocked by a manual session and updates saved preview without an SDK", async ({ page }) => {
  const state = await setup(page);
  state.active = true;
  await page.goto("/project?id=office-ui-test");
  await expect(page.getByText("Превью · v3", { exact: true })).toBeVisible();
  await page.getByTestId("chat-input").fill("/edit Измени заголовок");
  await page.getByTestId("chat-send").click();
  await expect(page.getByText("Завершите ручное редактирование во всех вкладках", { exact: false })).toBeVisible();
  expect(state.edits).toBe(0);
  state.active = false;
  await expect(page.getByText("Ручная сессия ещё открыта", { exact: false })).toHaveCount(0);
  await page.getByTestId("chat-input").fill("/edit Измени заголовок");
  await page.getByTestId("chat-send").click();
  await expect(page.getByText("Превью · v4", { exact: true })).toBeVisible();
  expect(state.edits).toBe(1);
  expect(state.configs).toBe(0);
});

test("failed preview retries without creating an editor session", async ({ page }) => {
  const state = await setup(page);
  state.failPreview = true;
  await page.goto("/project?id=office-ui-test");
  await expect(page.getByText("Ошибка превью", { exact: false })).toBeVisible();
  state.failPreview = false;
  await page.getByRole("button", { name: "Повторить превью" }).click();
  await expect(page.getByText("Превью · v3", { exact: true })).toBeVisible();
  expect(state.opened).toHaveLength(1);
  expect(state.configs).toBe(0);
});

test("selected object routes a plain chat command to the same saved revision without SDK", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/project?id=office-ui-test");
  const outline = page.getByTestId("object-outline-2");
  await expect(outline).toBeVisible();
  // Click the actual image coordinates, not an artificial DOM click on the outline.
  const box = await outline.boundingBox();
  expect(box).not.toBeNull();
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await expect(page.getByTestId("office-object-target")).toContainText("Слайд 1 · Заголовок · v3");
  await expect(outline).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Снять выбор объекта" }).click();
  await expect(page.getByTestId("office-object-target")).toHaveCount(0);
  await outline.focus();
  await page.keyboard.press("Enter");
  await page.getByTestId("chat-input").fill("Перенеси вправо");
  const request = page.waitForRequest("**/api/office/documents/preview-test/edit");
  await page.getByTestId("chat-send").click();
  expect((await request).postDataJSON()).toEqual({ revision: 3, instruction: "Перенеси вправо", target: { slide: 1, shape_id: "2" } });
  await expect(page.getByText("Превью · v4", { exact: true })).toBeVisible();
  await expect(page.getByTestId("office-object-target")).toHaveCount(0);
  expect(state.edits).toBe(1);
  expect(state.configs).toBe(0);
});

test("opening failure offers retry without silently changing the document", async ({ page }) => {
  await setup(page);
  await page.route("**/api/office/documents", (r) => r.fulfill({ status: 503, json: { error: { message: "Ошибка открытия" } } }));
  await page.goto("/project?id=office-ui-test");
  await expect(page.getByText("Ошибка открытия", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Повторить открытие" })).toBeEnabled();
  await expect(page.locator("iframe")).toHaveCount(0);
});

test("object map failure is retryable and a newer revision clears selection while preview fails", async ({ page }) => {
  const state = await setup(page);
  let fail = true;
  await page.route(/\/api\/office\/documents\/preview-test\/objects\/\d+$/, (r) => fail
    ? r.fulfill({ status: 503, json: { error: { message: "Карта объектов недоступна" } } })
    : r.fallback());
  await page.goto("/project?id=office-ui-test");
  await expect(page.getByText("Превью · v3", { exact: true })).toBeVisible();
  await expect(page.getByText("Выбор объектов недоступен:", { exact: false })).toBeVisible();
  fail = false;
  await page.getByRole("button", { name: "Повторить загрузку объектов" }).click();
  const outline = page.getByTestId("object-outline-2");
  await expect(outline).toBeVisible();
  await outline.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("office-object-target")).toBeVisible();
  state.failPreview = true;
  state.revision = 4;
  await expect(page.getByText("Обновляем превью v4", { exact: false })).toBeVisible();
  await expect(page.getByTestId("office-object-target")).toHaveCount(0);
  await expect(outline).toHaveCount(0);
  await page.getByTestId("chat-input").fill("/edit Перенеси правее");
  await page.getByTestId("chat-send").click();
  await expect(page.getByText("Дождитесь актуального превью перед ИИ-правкой.", { exact: true })).toBeVisible();
  expect(state.edits).toBe(0);
});
test("loading screen shows the opening stages in project style until slides are ready", async ({ page }) => {
  await setup(page);
  // Программа и слайды готовы не сразу: этапы переключаются событиями SDK.
  await page.route("**/office-test-sdk.js", (r) => r.fulfill({ contentType: "application/javascript", body: `
    window.DocsAPI = { DocEditor: function(id, config) {
      const frame = document.createElement('iframe'); document.getElementById(id).appendChild(frame);
      window.testAppReady = () => config.events.onAppReady();
      window.testDocReady = () => config.events.onDocumentReady();
      this.requestClose = () => config.events.onRequestClose();
      this.destroyEditor = () => frame.remove();
    }};
  ` }));
  await page.goto("/project?id=office-ui-test&officeView=editor");
  const loading = page.getByTestId("office-loading");
  await expect(loading).toBeVisible();
  await expect(loading.getByText("Запускаем редактор")).toBeVisible();
  await page.evaluate(() => (window as unknown as { testAppReady: () => void }).testAppReady());
  await expect(loading.getByText("Открываем слайды")).toBeVisible();
  await page.evaluate(() => (window as unknown as { testDocReady: () => void }).testDocReady());
  await expect(loading).toHaveCount(0);
  await expect(page.getByTestId("office-preview")).toBeEnabled();
});
