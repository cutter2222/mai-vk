import { expect, test, type Locator, type Page } from "@playwright/test";

// Готовый слайд шаблона из вкладки «Слайды» перетаскивается в открытую презентацию: сервер
// вставляет его копией после текущего слайда, редактор открывается уже на нём. SDK с мостом
// текущего слайда и API — заглушки теста.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC", "base64");

async function setup(page: Page) {
  const state = { docRevision: 1, inserts: [] as Record<string, unknown>[], fail: false };
  const createdAt = new Date().toISOString();
  const doc = () => ({ id: "slide-doc", source: "job_slides/balanced/r1/deck.pptx", title: "T", revision: state.docRevision, active_key: null, error: null, revisions: [] });
  await page.route("**/api/projects/slides-test", (r) => r.fulfill({ json: {
    schema_version: "1.5", project_id: "slides-test", title: "Футбол", template_id: "tpl_slides", package_id: null, job_id: "job_slides", chosen_variant: null,
    created_at: createdAt, updated_at: createdAt, brief: {}, settings: {}, events: [],
    files: [{ schema_version: "1.2", file_id: "file_tpl", name: "Шаблон.pptx", size_bytes: 2048, sha256: "f".repeat(64), mime: "application/octet-stream", kind: "template", template_id: "tpl_slides", added_at: createdAt, check: { status: "ok", format: "pptx" } }],
  } }));
  await page.route("**/api/projects/slides-test/events", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/templates/tpl_slides", (r) => r.fulfill({ json: {
    status: "succeeded", job_id: "job_tpl", name: "Шаблон", previews: [],
    profile: { assets: [], sample_slides: [
      { slide_index: 1, preview_path: "previews/slide-01.png", classification: "content_sample" },
      { slide_index: 2, preview_path: "previews/slide-02.png", classification: "style_guide" },
      { slide_index: 3, preview_path: "previews/slide-03.png", classification: "content_sample" },
    ] },
  } }));
  await page.route("**/api/templates/tpl_slides/assets/**", (r) => r.fulfill({ contentType: "image/png", body: PNG }));
  await page.route("**/api/generations/job_slides", (r) => r.fulfill({ json: {
    job_id: "job_slides", status: "succeeded", stage: "done", created_at: createdAt, metrics: { totals: { duration_ms: 1000 } }, execution_mode: { mode: "real", layers: {} },
    variants: [{ variant_id: "balanced", status: "ready", revision: 1, slide_count: 5, artifacts: { pptx: "balanced/r1/deck.pptx" } }],
  } }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: true } }));
  await page.route("**/api/office/documents", (r) => r.fulfill({ json: doc() }));
  await page.route("**/api/office/documents/slide-doc", (r) => r.fulfill({ json: doc() }));
  await page.route("**/api/office/documents/slide-doc/config", (r) => r.fulfill({ json: { script_url: "/slide-drop-sdk.js", config: {} } }));
  await page.route(/\/api\/office\/documents\/slide-doc\/objects\/\d+$/, (r) => r.fulfill({ json: { revision: state.docRevision, objects: [] } }));
  await page.route("**/api/office/documents/slide-doc/insert-slide", (r) => {
    state.inserts.push(r.request().postDataJSON());
    if (state.fail) return r.fulfill({ status: 422, json: { error: { code: "office_insert_failed", message: "Слайд не вставлен: в шаблоне нет слайда 3" } } });
    state.docRevision++;
    return r.fulfill({ json: { document: doc(), changed: true, message: "Слайд 3 шаблона вставлен после слайда 2." } });
  });
  // Поддельный редактор с мостом текущего слайда (как в office-live-selection).
  await page.route("**/slide-drop-sdk.js", (r) => r.fulfill({ contentType: "application/javascript", body: `
    window.__live = window.__live || { page: 0, count: 5, opened: 0, listeners: {} };
    window.DocsAPI = { DocEditor: function(id, config) {
      const live = window.__live;
      live.listeners = {};
      live.opened++;
      const frame = document.createElement('iframe'); document.getElementById(id).appendChild(frame);
      const on = (name) => live.listeners[name] || (live.listeners[name] = []);
      const emit = (name, ...args) => on(name).slice().forEach((fn) => fn(...args));
      const api = {
        asc_Save: () => false,
        isDocumentModified: () => false,
        asc_isDocumentCanSave: () => false,
        asc_registerCallback: (name, fn) => on(name).push(fn),
        asc_unregisterCallback: (name, fn) => { live.listeners[name] = on(name).filter((g) => g !== fn); },
        getCurrentPage: () => live.page,
        getCountPages: () => live.count,
        getSelectedElements: () => [{ get_ObjectType: () => 7, get_ObjectValue: () => ({}) }],
        asc_getSelectedDrawingObjectsCount: () => 0,
        goToPage: (index) => { live.page = index; emit('asc_onCurrentPage', index); },
      };
      frame.contentWindow.PE = { getController: () => ({ getApi: () => api }) };
      frame.contentWindow.Asc = { c_oAscAsyncAction: { Save: 1 }, c_oAscTypeSelectElement: { Paragraph: 0, Table: 1, Image: 2, Shape: 6, Slide: 7, Chart: 8 } };
      live.selectSlide = (index) => { live.page = index; emit('asc_onCurrentPage', index); emit('asc_onFocusObject', []); };
      setTimeout(() => config.events.onDocumentReady(), 0);
      this.requestClose = () => config.events.onRequestClose();
      this.destroyEditor = () => frame.remove();
    }};
    window.DocsAPI.DocEditor.warmUp = () => {};
  ` }));
  return state;
}

/** HTML5-перетаскивание по шагам: слой-приёмник появляется только после начала перетаскивания. */
async function dragToEditor(page: Page, source: Locator) {
  await source.hover();
  await page.mouse.down();
  const canvas = await page.locator(".office-canvas").boundingBox();
  await page.mouse.move(canvas!.x + canvas!.width / 2, canvas!.y + canvas!.height / 2, { steps: 8 });
  await expect(page.getByTestId("office-drop")).toBeVisible();
  await page.mouse.move(canvas!.x + canvas!.width / 2 + 10, canvas!.y + canvas!.height / 2 + 10, { steps: 2 });
  await page.mouse.up();
}

const live = (page: Page, script: string) => page.evaluate(script);

test("слайд шаблона встаёт после текущего, редактор открывается на нём", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/project?id=slides-test");
  await expect.poll(() => live(page, "window.__live?.opened ?? 0")).toBe(1);
  // Человек стоит на втором слайде.
  await live(page, "window.__live.selectSlide(1)");

  await page.getByTestId("tab-files").click();
  // PPTX шаблона в «Загруженных» нет: тащить его некуда, шаблон выбирают вверху справа.
  await expect(page.getByTestId("files-tab-uploaded")).toHaveText("Загруженные");
  await expect(page.getByTestId("file-file_tpl")).toHaveCount(0);
  // Во вкладке «Слайды» — образцы содержания, без страниц с правилами оформления.
  await expect(page.getByTestId("files-tab-slides")).toHaveText("Слайды · 2");
  await page.getByTestId("files-tab-slides").click();
  await expect(page.getByTestId("template-slides").locator(".slide-card")).toHaveCount(2);
  await expect(page.getByTestId("template-slide-2")).toHaveCount(0);
  await expect(page.getByTestId("template-slide-3").locator("img")).toHaveAttribute("src", /\/templates\/tpl_slides\/assets\/previews\/slide-03\.png$/);

  await page.getByTestId("template-slide-3").hover();
  await page.mouse.down();
  const canvas = await page.locator(".office-canvas").boundingBox();
  await page.mouse.move(canvas!.x + canvas!.width / 2, canvas!.y + canvas!.height / 2, { steps: 8 });
  await expect(page.getByTestId("office-drop")).toHaveText("Отпустите — «Слайд 3 шаблона» встанет после текущего слайда");
  await page.mouse.move(canvas!.x + canvas!.width / 2 + 10, canvas!.y + canvas!.height / 2 + 10, { steps: 2 });
  await page.mouse.up();

  // Сохранение, вставка на сервере, открытие редактора на вставленном (третьем) слайде.
  await expect.poll(() => state.inserts.length, { timeout: 20_000 }).toBe(1);
  expect(state.inserts[0]).toEqual({ revision: 1, template_id: "tpl_slides", slide: 3, after: 2 });
  await expect.poll(() => live(page, "window.__live.opened"), { timeout: 20_000 }).toBe(2);
  await expect.poll(() => live(page, "window.__live.page")).toBe(2);
});

test("отказ сервера — уведомление, редактор открывается снова", async ({ page }) => {
  const state = await setup(page);
  state.fail = true;
  await page.goto("/project?id=slides-test");
  await expect.poll(() => live(page, "window.__live?.opened ?? 0")).toBe(1);
  await page.getByTestId("tab-files").click();
  await page.getByTestId("files-tab-slides").click();
  await dragToEditor(page, page.getByTestId("template-slide-3"));
  await expect(page.getByText("Слайд не вставлен: в шаблоне нет слайда 3")).toBeVisible({ timeout: 20_000 });
  await expect.poll(() => live(page, "window.__live.opened"), { timeout: 20_000 }).toBe(2);
  expect(state.docRevision).toBe(1);
});
