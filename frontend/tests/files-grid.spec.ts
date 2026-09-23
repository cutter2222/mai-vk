import { expect, test, type Page } from "@playwright/test";

// Панель «Файлы» сеткой: загруженные файлы с миниатюрами сервера и ресурсы шаблона проекта.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC", "base64");

const file = (file_id: string, name: string, format: string, extra: Record<string, unknown> = {}) => ({
  schema_version: "1.2", file_id, name, size_bytes: 2048, sha256: file_id.padEnd(64, "0"), mime: "application/octet-stream",
  kind: "material", added_at: "2026-09-24T10:00:00Z", check: { status: "ok", format }, ...extra,
});

const asset = (asset_id: string, kind: string, media_path: string, tags: string[], extra: Record<string, unknown> = {}) => ({
  asset_id, kind, media_path, sha256: `sha-${asset_id}`, tags, ...extra,
});

async function setup(page: Page, templateStatus: "succeeded" | "running" = "succeeded") {
  const requests: string[] = [];
  await page.route("**/api/projects/files-grid-test", (r) => r.fulfill({ json: {
    schema_version: "1.5", project_id: "files-grid-test", title: "Сетка файлов", template_id: "tpl_grid", package_id: null, job_id: null, chosen_variant: null,
    created_at: "2026-09-24T10:00:00Z", updated_at: "2026-09-24T10:00:00Z", brief: {}, settings: {}, events: [],
    files: [
      file("file_photo", "фото команды.png", "image"),
      file("file_notes", "заметки.docx", "docx"),
      file("file_deck", "шаблон.pptx", "pptx", { kind: "template", template_id: "tpl_grid" }),
    ],
  } }));
  await page.route("**/api/projects/files-grid-test/events", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/templates/tpl_grid", (r) => r.fulfill({ json: {
    status: templateStatus, job_id: "job_tpl", name: "Шаблон", previews: [],
    profile: templateStatus === "succeeded" ? { assets: [
      asset("a_icon", "icon", "ppt/media/image1.png", ["рост", "график"]),
      asset("a_logo", "logo", "ppt/media/image2.png", ["логотип"]),
      asset("a_photo", "photo", "ppt/media/image3.jpeg", ["команда", "люди"]),
      asset("a_emf", "image", "ppt/media/image4.emf", ["вектор"]),
      asset("a_copy", "icon", "ppt/media/image5.png", ["рост"], { sha256: "sha-a_icon" }),
      asset("a_private", "photo", "ppt/media/image6.png", ["фон"], { reusable: false }),
    ] } : undefined,
  } }));
  await page.route(/\/api\/(projects\/files-grid-test\/files\/[^/]+\/thumbnail|templates\/tpl_grid\/media\/[^/]+(\/thumbnail)?)$/, (r) => {
    requests.push(new URL(r.request().url()).pathname);
    return r.fulfill({ contentType: "image/png", body: PNG });
  });
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: false } }));
  return requests;
}

test("загруженные файлы — карточки с миниатюрами сервера, остальным значок типа", async ({ page }) => {
  const requests = await setup(page);
  await page.goto("/project?id=files-grid-test");
  await page.getByTestId("tab-files").click();
  const panel = page.getByTestId("files-panel");
  await expect(panel).toContainText("Изображения · 1");
  await expect(panel).toContainText("Презентации · 1");
  await expect(panel).toContainText("Документы · 1");
  await expect(page.getByTestId("file-file_photo").locator("img")).toHaveAttribute("src", /\/files\/file_photo\/thumbnail$/);
  await expect(page.getByTestId("file-file_deck").locator("img")).toBeVisible();
  // Для DOCX сервер миниатюру не строит: запроса нет, вместо картинки значок.
  await expect(page.getByTestId("file-file_notes").locator("img")).toHaveCount(0);
  expect(requests.some((path) => path.includes("file_notes"))).toBe(false);
  await expect(page.getByTestId("file-file_photo").locator("a.file-thumb")).toHaveAttribute("href", /\/files\/file_photo\/content$/);
  await expect(page.getByTestId("files-tab-template")).toHaveText("Из шаблона · 3");
});

test("ресурсы шаблона — без векторных, повторов и закрытых; фильтр по виду и поиск по тегам", async ({ page }) => {
  await setup(page);
  await page.goto("/project?id=files-grid-test");
  await page.getByTestId("tab-files").click();
  await page.getByTestId("files-tab-template").click();
  const grid = page.getByTestId("template-assets");
  await expect(grid.locator(".asset-card")).toHaveCount(3);
  await expect(page.getByTestId("template-asset-a_emf")).toHaveCount(0);
  await expect(page.getByTestId("template-asset-a_copy")).toHaveCount(0);
  await expect(page.getByTestId("template-asset-a_private")).toHaveCount(0);
  await expect(page.getByTestId("template-asset-a_icon").locator("img")).toHaveAttribute("src", /\/templates\/tpl_grid\/media\/a_icon\/thumbnail$/);

  await page.getByTestId("asset-kind-logo").click();
  await expect(grid.locator(".asset-card")).toHaveCount(1);
  await expect(page.getByTestId("template-asset-a_logo")).toBeVisible();
  await page.getByText("Все", { exact: true }).click();
  await page.getByTestId("asset-search").fill("люд");
  await expect(grid.locator(".asset-card")).toHaveCount(1);
  await expect(page.getByTestId("template-asset-a_photo")).toBeVisible();

  // Загруженные файлы — на своей вкладке, кнопка добавления тоже там.
  await page.getByTestId("files-tab-uploaded").click();
  await expect(page.getByTestId("files-add")).toBeVisible();
  await expect(page.getByTestId("template-assets")).toHaveCount(0);
});

test("шаблон ещё разбирается — вкладка ресурсов ждёт его", async ({ page }) => {
  await setup(page, "running");
  await page.goto("/project?id=files-grid-test");
  await page.getByTestId("tab-files").click();
  await expect(page.getByTestId("files-tab-template")).toHaveText("Из шаблона");
  await page.getByTestId("files-tab-template").click();
  await expect(page.getByTestId("files-panel")).toContainText("Шаблон разбирается");
});

test("добавление из панели и удаление карточки", async ({ page }) => {
  await setup(page);
  const deleted: string[] = [];
  await page.route("**/api/projects/files-grid-test/files", (r) => r.fulfill({ status: 201, json: [file("file_new", "итоги.png", "image", { kind: "other" })] }));
  await page.route("**/api/projects/files-grid-test/files/file_new", (r) => {
    if (r.request().method() === "DELETE") { deleted.push("file_new"); return r.fulfill({ status: 204 }); }
    return r.fulfill({ json: file("file_new", "итоги.png", "image", { kind: "other" }) });
  });
  await page.goto("/project?id=files-grid-test");
  await page.getByTestId("tab-files").click();
  const [chooser] = await Promise.all([page.waitForEvent("filechooser"), page.getByTestId("files-add").click()]);
  await chooser.setFiles([{ name: "итоги.png", mimeType: "image/png", buffer: PNG }]);
  const card = page.getByTestId("file-file_new");
  await expect(card).toBeVisible();
  await expect(page.getByTestId("files-panel")).toContainText("Изображения · 2");
  await card.hover();
  await card.getByTestId("file-remove-file_new").click();
  await expect(card).toHaveCount(0);
  expect(deleted).toEqual(["file_new"]);
});
