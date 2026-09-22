import { expect, test } from "@playwright/test";

for (const layoutsOnly of [false, true]) {
  test(`project template preview ${layoutsOnly ? "does not present layouts as slides" : "shows only real slides in numeric order"}`, async ({ page }) => {
    await page.route("**/api/projects/template-preview-test", (r) => r.fulfill({ json: {
      project_id: "template-preview-test", title: "Шаблон", template_id: "tpl_previewtest",
      files: [], brief: {}, settings: {}, events: [],
    } }));
    await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
    await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: false } }));
    await page.route("**/api/templates/tpl_previewtest", (r) => r.fulfill({ json: {
      status: "succeeded", name: "Шаблон.pptx",
      previews: ["previews/layout-slideLayout1.png", "previews/layout-slideLayout10.png",
        ...(layoutsOnly ? [] : ["previews/slide-100.png", "previews/slide-01.png", "previews/slide-02.png"])],
    } }));
    const assets: string[] = [];
    await page.route("**/api/templates/tpl_previewtest/assets/**", (r) => {
      assets.push(r.request().url());
      return r.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4ZkAAAAASUVORK5CYII=", "base64") });
    });
    await page.goto("/project?id=template-preview-test");
    if (layoutsOnly) {
      await expect(page.getByTestId("slide-blank")).toBeVisible();
      await expect(page.getByTestId("project-template-preview")).toHaveCount(0);
    } else {
      const preview = page.getByTestId("project-template-preview");
      await expect(preview.getByTestId("slide-counter")).toHaveText("Слайд 1 из 3");
      await expect(preview.locator(".filmstrip button")).toHaveCount(3);
      for (const [i, name] of ["slide-01.png", "slide-02.png", "slide-100.png"].entries()) {
        await expect(preview.getByTestId(`thumb-${i}`).locator("img")).toHaveAttribute("src", new RegExp(`${name}$`));
      }
      await preview.getByTestId("thumb-1").click();
      await expect(preview.locator(".preview-stage img")).toHaveAttribute("src", /slide-02\.png$/);
      await expect(preview.getByTestId("slide-counter")).toHaveText("Слайд 2 из 3");
    }
    expect(assets.some((url) => url.includes("layout-"))).toBe(false);
    await expect(page.locator("iframe")).toHaveCount(0);
  });
}

test("template working copy selects three objects and sends one scoped AI command", async ({ page }) => {
  const doc = { id: "template-copy", revision: 0, active_key: null, error: null };
  const objects = [2, 3, 4].map((id, i) => ({ slide: 1, shape_id: String(id), label: `Объект ${id}`, kind: "sp", bbox: { x: 0.1, y: 0.1 + i * 0.25, width: 0.3, height: 0.15 }, z: i, hollow: false }));
  await page.route("**/api/projects/template-select-test", (r) => r.fulfill({ json: {
    project_id: "template-select-test", title: "Шаблон", template_id: "tpl_select", files: [], brief: {}, settings: {}, events: [],
  } }));
  await page.route("**/api/projects/template-select-test/events", (r) => r.fulfill({ json: { ...r.request().postDataJSON(), event_id: String(Date.now()), at: new Date().toISOString() } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/templates/tpl_select", (r) => r.fulfill({ json: {
    status: "succeeded", name: "Шаблон.pptx", previews: ["previews/layout-slideLayout1.png", "previews/slide-02.png", "previews/slide-01.png"],
  } }));
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: true } }));
  await page.route("**/api/office/projects/template-select-test/template", (r) => {
    expect(r.request().postDataJSON()).toEqual({ template_id: "tpl_select" });
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
  await page.goto("/project?id=template-select-test");
  await expect(page.getByTestId("slide-counter")).toHaveText("Слайд 1 из 2");
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
  await click(3, "Control");
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