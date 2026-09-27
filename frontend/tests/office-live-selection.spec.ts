import { expect, test, type Page } from "@playwright/test";

/**
 * Живой редактор ONLYOFFICE сообщает текущий слайд и выделение своими внутренними событиями
 * (`asc_onCurrentPage`, `asc_onFocusObject`); здесь их шлёт поддельный редактор с тем же API.
 * Выбранный слайд — адресат правки из чата: плашка, подсказка, правка слайда, пересобранный
 * слайд встаёт в открытую копию, и редактор возвращается на тот же слайд. Выделенный объект —
 * правка только его, сервер находит фигуру по имени и рамке.
 */
async function setup(page: Page, { slides = 5, editorSlides = 5 } = {}) {
  const state = {
    revision: 1, docRevision: 1, edits: [] as Record<string, unknown>[], editBodies: [] as Record<string, unknown>[],
    applied: [] as Record<string, unknown>[], objectEdits: [] as Record<string, unknown>[], editDone: false,
    chats: [] as Record<string, unknown>[], routes: [] as Record<string, unknown>[], undos: [] as Record<string, unknown>[],
    texts: {} as Record<string, string>,
  };
  const createdAt = new Date().toISOString();
  const doc = () => ({ id: "live-doc", revision: state.docRevision, active_key: null, error: null });
  await page.route("**/api/projects/live-test", (r) => r.fulfill({ json: {
    project_id: "live-test", title: "Футбол", job_id: "job_live", files: [], brief: {}, settings: {}, events: [], created_at: createdAt,
  } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: true, script_url: "/live-sdk.js" } }));
  await page.route("**/api/generations/job_live", (r) => r.fulfill({ json: {
    job_id: "job_live", status: "succeeded", stage: "done", created_at: createdAt,
    metrics: { totals: { duration_ms: 1000 } }, execution_mode: { mode: "real", layers: {} },
    versions: { app: "test", contracts: "1.11", skills: [], prompts: [], models: [] },
    variants: [{ variant_id: "balanced", status: "ready", revision: state.revision, slide_count: slides, artifacts: { pptx: `balanced/r${state.revision}/deck.pptx`, thumbnails: [] } }],
    edits: state.edits,
  } }));
  await page.route("**/api/generations/job_live/variants/balanced/edits", (r) => {
    state.editBodies.push(r.request().postDataJSON());
    return r.fulfill({ json: { edit_job_id: "edit_1" } });
  });
  await page.route("**/api/jobs/edit_1", (r) => {
    if (!state.editDone) {
      // Правка готова со второго опроса: ревизия 2, пересобранный слайд 3.
      state.editDone = true;
      state.revision = 2;
      state.edits.push({ edit_job_id: "edit_1", variant_id: "balanced", base_revision: 1, slide_index: 2, instruction: "", result: "applied", new_revision: 2, change_note: "Две колонки вместо четырёх" });
      return r.fulfill({ json: { job_id: "edit_1", status: "running" } });
    }
    return r.fulfill({ json: { job_id: "edit_1", status: "succeeded", result: { revision: 2, change_note: "Две колонки вместо четырёх" } } });
  });
  await page.route("**/api/office/documents", (r) => r.fulfill({ json: doc() }));
  await page.route("**/api/office/documents/live-doc", (r) => r.fulfill({ json: doc() }));
  await page.route("**/api/office/documents/live-doc/config", (r) => r.fulfill({ json: { script_url: "/live-sdk.js", config: {} } }));
  await page.route(/\/api\/office\/documents\/live-doc\/objects\/\d+$/, (r) => r.fulfill({ json: {
    revision: state.docRevision,
    objects: [
      { slide: 3, shape_id: "7", label: "Идея: плотность в центре снижает атаки", name: "Заголовок 1", kind: "sp", bbox: { x: 0.05, y: 0.05, width: 0.6, height: 0.1 }, z: 1, hollow: false, placeholder: "title" },
      // Одноимённая фигура верхнего уровня и фигура в группе: подпись берётся у той, что в группе.
      { slide: 3, shape_id: "40", label: "Шаг", name: "Google Shape;587;p54", kind: "sp", bbox: { x: 0.7, y: 0.8, width: 0.1, height: 0.05 }, z: 2, hollow: false },
      { slide: 3, shape_id: "587", label: "Название команды", name: "Google Shape;587;p54", kind: "sp", bbox: { x: 0.238, y: 0.204, width: 0.12, height: 0.062 }, z: 5, hollow: false, group_path: ["581"] },
    ],
  } }));
  await page.route("**/api/projects/live-test/events", (r) => {
    const body = r.request().postDataJSON() as Record<string, unknown>;
    const eventId = `evt_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
    if (body.role === "user") state.texts[eventId] = String(body.text ?? "");
    return r.fulfill({ json: { ...body, event_id: eventId, created_at: createdAt } });
  });
  await page.route(/\/api\/projects\/live-test\/events\/[^/]+$/, (r) => r.fulfill({ json: { event_id: "patched", ...(r.request().postDataJSON() as object) } }));
  // Роутер (этап 37) — те же правила, что на сервере, для этих сценариев: плашка объекта —
  // правка объекта, плашка слайда — перестройка, вопрос — ассистенту, «отмени» — отмена.
  await page.route("**/api/chat/route", (r) => {
    const body = r.request().postDataJSON() as { event_id: string; chip?: { slide: number; object?: Record<string, unknown> } };
    state.routes.push(body);
    const text = (state.texts[body.event_id] ?? "").replace(/^Слайд \d+ · [^\n]*\n/, "");
    const decision = (kind: string, steps: unknown[] = []) => r.fulfill({ json: { kind, steps, text: "", options: [], source: "rules", normalized: text } });
    if (/^отмени/i.test(text)) return decision("run", [{ action: "undo", slides: [] }]);
    if (/\?\s*$|^что/i.test(text)) return decision("answer");
    if (body.chip?.object) return decision("run", [{ action: "object_edit", slides: [body.chip.slide], instruction: text, target: { slide: body.chip.slide, ...body.chip.object } }]);
    if (body.chip) return decision("run", [{ action: "slide_rebuild", slides: [body.chip.slide], instruction: text }]);
    return decision("answer");
  });
  await page.route("**/api/office/documents/live-doc/undo", (r) => {
    state.undos.push(r.request().postDataJSON());
    state.docRevision++;
    return r.fulfill({ json: { document: doc(), changed: true, message: "Правка отменена." } });
  });
  await page.route("**/api/chat", (r) => {
    state.chats.push(r.request().postDataJSON());
    return r.fulfill({ json: { reply: "Слайд 3 «Идея»: две карточки.", options: [], source: "rules", event: { event_id: "evt_reply", role: "assistant", kind: "text", text: "Слайд 3 «Идея»: две карточки.", created_at: createdAt } } });
  });
  await page.route("**/api/office/documents/live-doc/apply-slide", (r) => {
    state.applied.push(r.request().postDataJSON());
    state.docRevision++;
    return r.fulfill({ json: { document: doc(), changed: true, message: "Слайд 3 обновлён." } });
  });
  await page.route("**/api/office/documents/live-doc/edit", (r) => {
    state.objectEdits.push(r.request().postDataJSON());
    state.docRevision++;
    return r.fulfill({ json: { document: doc(), changed: true, message: "Заголовок сокращён" } });
  });
  // Поддельный редактор: сохранение (как в onlyoffice-project) и мост выделения.
  await page.route("**/live-sdk.js", (r) => r.fulfill({ contentType: "application/javascript", body: `
    window.__live = window.__live || { page: 0, count: ${editorSlides}, selected: [], opened: 0, listeners: {} };
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
        getSelectedElements: () => live.selected.map((o) => ({ get_ObjectType: () => o.type, get_ObjectValue: () => o.value })),
        asc_getSelectedDrawingObjectsCount: () => live.selected.filter((o) => o.type === 6).length,
        goToPage: (index) => { live.page = index; emit('asc_onCurrentPage', index); },
      };
      frame.contentWindow.PE = { getController: () => ({ getApi: () => api }) };
      frame.contentWindow.Asc = { c_oAscAsyncAction: { Save: 1 }, c_oAscTypeSelectElement: { Paragraph: 0, Table: 1, Image: 2, Shape: 6, Slide: 7, Chart: 8 } };
      live.selectSlide = (index) => { live.page = index; live.selected = [{ type: 7, value: {} }]; emit('asc_onCurrentPage', index); emit('asc_onFocusObject', []); };
      live.selectTitle = () => {
        live.selected = [{ type: 7, value: {} }, { type: 6, value: {
          asc_getName: () => 'Заголовок 1', asc_getWidth: () => 200, asc_getHeight: () => 30,
          asc_getPosition: () => ({ get_X: () => 20, get_Y: () => 10 }),
        } }, { type: 0, value: {} }];
        emit('asc_onFocusObject', []);
      };
      // Щелчок по тексту фигуры в группе выделяет её саму: имя её, рамка — от угла группы.
      live.selectGroupChild = () => {
        live.selected = [{ type: 7, value: {} }, { type: 6, value: {
          asc_getName: () => 'Google Shape;587;p54', asc_getFromGroup: () => true, asc_getWidth: () => 30.6, asc_getHeight: () => 8.9,
          asc_getPosition: () => ({ get_X: () => 52.75, get_Y: () => 0 }),
        } }];
        emit('asc_onFocusObject', []);
      };
      setTimeout(() => config.events.onDocumentReady(), 0);
      this.requestClose = () => config.events.onRequestClose();
      this.destroyEditor = () => frame.remove();
    }};
    window.DocsAPI.DocEditor.warmUp = () => {};
  ` }));
  return state;
}

const live = (page: Page, script: string) => page.evaluate(script);

test("undo from another document cannot mutate the open copy", async ({ page }) => {
  const state = await setup(page);
  await page.route("**/api/projects/live-test", (r) => r.fulfill({ json: {
    project_id: "live-test", title: "Футбол", job_id: "job_live", files: [], brief: {}, settings: {},
    events: [{ event_id: "other-edit", role: "assistant", kind: "edit_result", text: "Правка другой копии",
      document_id: "other-doc", revision: 1, base_revision: 0, slides: [1] }],
  } }));
  await page.goto("/project?id=live-test");
  await expect.poll(() => live(page, "window.__live?.opened ?? 0")).toBe(1);
  await page.getByTestId("edit-result-undo").click();
  await expect(page.getByTestId("chat-list")).toContainText("Откройте презентацию, в которой была сделана эта правка");
  expect(state.undos).toEqual([]);
});

test("выбранный в редакторе слайд — адресат правки: слайд пересобирается и встаёт в открытую копию", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/project?id=live-test");
  await expect.poll(() => live(page, "window.__live?.opened ?? 0")).toBe(1);
  await live(page, "window.__live.selectSlide(2)");
  const chip = page.getByTestId("live-target");
  await expect(chip).toHaveText("Слайд 3 · Сбалансированный");
  await expect(chip).toHaveAttribute("data-kind", "slide");
  const input = page.getByTestId("chat-input");
  await expect(input).toHaveAttribute("placeholder", "Что изменить на слайде 3?");
  await input.fill("сделай не 4 колонки, а 2");
  await page.getByTestId("chat-send").click();
  await expect.poll(() => state.editBodies.length).toBe(1);
  expect(state.editBodies[0]).toMatchObject({ base_revision: 1, slide_index: 2, instruction: "сделай не 4 колонки, а 2" });
  // Пересобранный слайд — в открытую копию: сохранение, перенос, открытие на том же слайде.
  await expect.poll(() => state.applied.length, { timeout: 20_000 }).toBe(1);
  expect(state.applied[0]).toEqual({ revision: 1, job_id: "job_live", variant_id: "balanced", artifact_revision: 2, slide: 3, source_slide: 3 });
  await expect.poll(() => live(page, "window.__live.opened"), { timeout: 20_000 }).toBe(2);
  await expect.poll(() => live(page, "window.__live.page")).toBe(2);
  await expect(page.getByText("Доступна другая версия презентации")).toHaveCount(0);
});

test("выделенный объект — правка только его; крестик снимает адресацию", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/project?id=live-test");
  await expect.poll(() => live(page, "window.__live?.opened ?? 0")).toBe(1);
  await live(page, "window.__live.selectSlide(2)");
  await live(page, "window.__live.selectTitle()");
  const chip = page.getByTestId("live-target");
  await expect(chip).toHaveText("Слайд 3 · «Идея: плотность в центре снижает атаки»");
  await expect(chip).toHaveAttribute("data-kind", "object");
  await expect(page.getByTestId("chat-input")).toHaveAttribute("placeholder", "Что изменить в выделенном объекте?");
  await page.getByTestId("chat-input").fill("сократи");
  await page.getByTestId("chat-send").click();
  await expect.poll(() => state.objectEdits.length, { timeout: 20_000 }).toBe(1);
  expect(state.objectEdits[0]).toEqual({ revision: 1, instruction: "сократи", live_target: { slide: 3, name: "Заголовок 1", box: { x: 20, y: 10, width: 200, height: 30 } } });
  await expect(page.getByTestId("msg-user").last()).toContainText("Слайд 3 · «Идея: плотность в центре снижает атаки»");
  expect(state.editBodies).toHaveLength(0);
  // Крестик: сообщение уходит в обычный чат, пока в редакторе не выберут другое.
  await expect(page.getByTestId("live-target")).toBeVisible({ timeout: 20_000 });
  await page.getByTestId("live-target-dismiss").click();
  await expect(page.getByTestId("live-target")).toHaveCount(0);
  await live(page, "window.__live.selectSlide(1)");
  await expect(page.getByTestId("live-target")).toHaveText("Слайд 2 · Сбалансированный");
});

test("слайды добавлены в редакторе вручную — пересобрать слайд из чата нельзя, об этом сказано", async ({ page }) => {
  const state = await setup(page, { slides: 5, editorSlides: 6 });
  await page.goto("/project?id=live-test");
  await expect.poll(() => live(page, "window.__live?.opened ?? 0")).toBe(1);
  await live(page, "window.__live.selectSlide(2)");
  await expect(page.getByTestId("live-target")).toHaveText("Слайд 3 · Сбалансированный");
  await page.getByTestId("chat-input").fill("сделай две колонки");
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("chat-list")).toContainText("Слайды в редакторе добавлены или удалены вручную");
  expect(state.editBodies).toHaveLength(0);
});

test("фигура в группе — плашка с её текстом, правка находит её по имени и рамке группы", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/project?id=live-test");
  await expect.poll(() => live(page, "window.__live?.opened ?? 0")).toBe(1);
  await live(page, "window.__live.selectSlide(2)");
  await live(page, "window.__live.selectGroupChild()");
  const chip = page.getByTestId("live-target");
  await expect(chip).toHaveText("Слайд 3 · «Название команды»");
  await page.getByTestId("chat-input").fill("сократи");
  await page.getByTestId("chat-send").click();
  await expect.poll(() => state.objectEdits.length, { timeout: 20_000 }).toBe(1);
  expect(state.objectEdits[0]).toEqual({ revision: 1, instruction: "сократи", live_target: { slide: 3, name: "Google Shape;587;p54", box: { x: 52.75, y: 0, width: 30.6, height: 8.9 }, in_group: true } });
});

test("вопрос при плашке слайда уходит ассистенту вместе с открытой копией, слайд не пересобирается", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/project?id=live-test");
  await expect.poll(() => live(page, "window.__live?.opened ?? 0")).toBe(1);
  await live(page, "window.__live.selectSlide(2)");
  await expect(page.getByTestId("live-target")).toHaveText("Слайд 3 · Сбалансированный");
  await page.getByTestId("chat-input").fill("что на этом слайде?");
  await page.getByTestId("chat-send").click();
  await expect.poll(() => state.chats.length, { timeout: 20_000 }).toBe(1);
  expect(state.chats[0]).toMatchObject({ project_id: "live-test", office: { document_id: "live-doc", revision: 1 } });
  await expect(page.getByTestId("chat-list")).toContainText("Слайд 3 «Идея»: две карточки.");
  expect(state.editBodies).toHaveLength(0);
});

test("правка объекта — карточка с «Отменить»: кнопка и слово «отмени» возвращают копию", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/project?id=live-test");
  await expect.poll(() => live(page, "window.__live?.opened ?? 0")).toBe(1);
  await live(page, "window.__live.selectSlide(2)");
  await live(page, "window.__live.selectTitle()");
  await page.getByTestId("chat-input").fill("сократи");
  await page.getByTestId("chat-send").click();
  const card = page.getByTestId("edit-result").last();
  await expect(card).toContainText("Слайд 3: заголовок сокращён", { timeout: 20_000 });
  await card.getByTestId("edit-result-undo").click();
  await expect.poll(() => state.undos.length, { timeout: 20_000 }).toBe(1);
  expect(state.undos[0]).toEqual({ revision: 2, to_revision: 1 });
  await expect(card).toHaveAttribute("data-undone", "true");
  await expect(card).toContainText("Отменено");

  // Вторая правка и «отмени» словом: отменяется последняя неотменённая.
  await expect(page.getByTestId("live-target")).toBeVisible({ timeout: 20_000 });
  await live(page, "window.__live.selectTitle()");
  await page.getByTestId("chat-input").fill("сократи ещё");
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("edit-result")).toHaveCount(2, { timeout: 20_000 });
  await page.getByTestId("live-target-dismiss").click();
  await page.getByTestId("chat-input").fill("отмени");
  await page.getByTestId("chat-send").click();
  await expect.poll(() => state.undos.length, { timeout: 20_000 }).toBe(2);
  expect(state.undos[1]).toEqual({ revision: 4, to_revision: 3 });
  await expect(page.getByTestId("edit-result").last()).toHaveAttribute("data-undone", "true");
});
