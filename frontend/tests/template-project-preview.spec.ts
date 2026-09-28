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
