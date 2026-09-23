import { expect, test } from "@playwright/test";

for (const officeEnabled of [false, true]) {
  test(`selected template never appears as generated slides (office=${officeEnabled})`, async ({ page }) => {
    await page.route("**/api/projects/template-preview-test", (r) => r.fulfill({ json: {
      project_id: "template-preview-test", title: "Шаблон", template_id: "tpl_previewtest",
      files: [], brief: {}, settings: {}, events: [],
    } }));
    await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
    await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: officeEnabled } }));
    await page.route("**/api/templates/tpl_previewtest", (r) => r.fulfill({ json: {
      status: "succeeded", name: "Шаблон.pptx",
      previews: ["previews/layout-slideLayout1.png", "previews/layout-slideLayout10.png",
        "previews/slide-100.png", "previews/slide-01.png", "previews/slide-02.png"],
    } }));
    const assets: string[] = [];
    const copies: string[] = [];
    await page.route("**/api/office/projects/*/template", (r) => {
      copies.push(r.request().url());
      return r.fulfill({ status: 500 });
    });
    await page.route("**/api/templates/tpl_previewtest/assets/**", (r) => {
      assets.push(r.request().url());
      return r.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4ZkAAAAASUVORK5CYII=", "base64") });
    });
    await page.goto("/project?id=template-preview-test");
    await expect(page.getByTestId("preview-empty")).toContainText("Здесь появится ваша презентация");
    await expect(page.getByTestId("preview-pane").locator("img")).toHaveCount(0);
    await expect(page.getByTestId("slide-counter")).toHaveCount(0);
    expect(assets).toEqual([]);
    expect(copies).toEqual([]);
    await expect(page.locator("iframe")).toHaveCount(0);
  });
}

test("generated presentation selects three objects and sends one scoped AI command", async ({ page }) => {
  const doc = { id: "template-copy", revision: 0, active_key: null, error: null };
  const objects = [2, 3, 4].map((id, i) => ({ slide: 1, shape_id: String(id), label: `Объект ${id}`, kind: "sp", bbox: { x: 0.1, y: 0.1 + i * 0.25, width: 0.3, height: 0.15 }, z: i, hollow: false }));
  await page.route("**/api/projects/template-select-test", (r) => r.fulfill({ json: {
    project_id: "template-select-test", title: "Презентация", template_id: "tpl_select", job_id: "job_generated", files: [], brief: {}, settings: {}, events: [],
  } }));
  await page.route("**/api/projects/template-select-test/events", (r) => r.fulfill({ json: { ...r.request().postDataJSON(), event_id: String(Date.now()), at: new Date().toISOString() } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/templates/tpl_select", (r) => r.fulfill({ json: {
    status: "succeeded", name: "Шаблон.pptx", previews: ["previews/layout-slideLayout1.png", "previews/slide-02.png", "previews/slide-01.png"],
  } }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: true } }));
  await page.route("**/api/generations/job_generated", (r) => r.fulfill({ json: {
    job_id: "job_generated", status: "succeeded", stage: "done", variants: [{ variant_id: "balanced", status: "ready", revision: 1, artifacts: { pptx: "balanced/r1/deck.pptx" } }],
    metrics: { totals: { duration_ms: 1000 } }, execution_mode: { mode: "real", layers: {} },
  } }));
  await page.route("**/api/office/documents", (r) => {
    expect(r.request().postDataJSON()).toMatchObject({ job_id: "job_generated", artifact: "balanced/r1/deck.pptx" });
    return r.fulfill({ json: doc });
  });
  await page.route("**/api/office/documents/template-copy", (r) => r.fulfill({ json: doc }));
  await page.route(/\/api\/office\/documents\/template-copy\/objects\/\d+$/, (r) => r.fulfill({ json: { revision: doc.revision, objects } }));
  await page.route("**/*.png", (r) => r.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4ZkAAAAASUVORK5CYII=", "base64") }));
  await page.route(/\/api\/office\/documents\/template-copy\/preview\/\d+$/, (r) => r.fulfill({ json: { revision: doc.revision, slides: ["slide-01.png", "slide-02.png"], ratio: 16 / 9 } }));
  let edits = 0;
  await page.route("**/api/office/documents/template-copy/edit", (r) => {
    edits++;
    expect(r.request().postDataJSON()).toEqual({ revision: 0, instruction: "Все три правее", targets: objects.map(({ slide, shape_id }) => ({ slide, shape_id })) });
    doc.revision = 1;
    return r.fulfill({ json: { document: doc, changed: true, message: "Три объекта перемещены" } });
  });
  await page.goto("/project?id=template-select-test&officeView=preview");
  await expect(page.getByTestId("slide-counter")).toHaveText("Слайд 1 из 2");
  // Правая панель занята презентацией: выбор шаблона — в шапке, а не в ней.
  await expect(page.locator(".editor-header").getByTestId("template-menu")).toBeVisible();
  await expect(page.getByTestId("preview-pane").getByTestId("template-menu")).toHaveCount(0);
  const click = async (id: number, modifier?: "Shift" | "Control" | "Meta") => {
    const outline = page.getByTestId(`object-outline-${id}`);
    await expect(outline).toBeVisible();
    const box = (await outline.boundingBox())!;
    if (modifier) await page.keyboard.down(modifier);
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    if (modifier) await page.keyboard.up(modifier);
  };
  await click(2);
  await click(3, "Shift");
  await click(4, "Meta");
  await expect(page.locator('.object-outline[aria-pressed="true"]')).toHaveCount(3);
  await expect(page.getByTestId("office-object-target")).toContainText("Выбрано объектов: 3");
  await expect(page.getByTestId("office-object-target")).toContainText("Правка только выбранных объектов");
  await expect(page.getByTestId("object-outline-2")).toHaveCSS("border-top-width", "2px");
  // macOS treats Control+click as a native context-menu click, not a left click.
  await page.getByTestId("object-outline-3").dispatchEvent("click", { ctrlKey: true });
  await expect(page.locator('.object-outline[aria-pressed="true"]')).toHaveCount(2);
  await click(2); // ordinary click replaces selection
  await expect(page.locator('.object-outline[aria-pressed="true"]')).toHaveCount(1);
  await page.getByTestId("multi-select").click();
  await click(3);
  await click(4);
  await page.getByPlaceholder("Что изменить в выбранных объектах или куда их переместить?").fill("Все три правее");
  await page.getByTestId("chat-send").click();
  await expect(page.getByText(/Три объекта перемещены/)).toBeVisible();
  expect(edits).toBe(1);
  await expect(page.getByTestId("office-object-target")).toHaveCount(0);
  await expect(page.getByText("Превью · v1", { exact: true })).toBeVisible();
  await click(2);
  await page.getByTestId("thumb-1").click();
  await expect(page.getByTestId("office-object-target")).toHaveCount(0);
  await expect(page.locator("iframe")).toHaveCount(0);
});