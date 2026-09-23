import { expect, test } from "@playwright/test";

test("template picker in the empty preview selects an existing template or uploads a new one without a PPTX question", async ({ page }) => {
  const project = { project_id: "template-start-test", title: "Новая презентация", files: [] as Record<string, unknown>[], events: [] as Record<string, unknown>[], brief: {}, settings: {}, template_id: null as string | null };
  let uploaded = false;
  let generations = 0;
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: false } }));
  await page.route("**/api/projects/template-start-test", async (r) => {
    if (r.request().method() === "PATCH") Object.assign(project, r.request().postDataJSON());
    await r.fulfill({ json: project });
  });
  await page.route("**/api/projects/template-start-test/events", async (r) => {
    const event = { ...r.request().postDataJSON(), event_id: `event-${project.events.length}`, at: new Date().toISOString() };
    project.events.push(event);
    await r.fulfill({ json: event });
  });
  await page.route("**/api/projects/template-start-test/files", (r) => {
    uploaded = true;
    const files = [{ file_id: "file-template", name: "Новый.pptx", size_bytes: 12, kind: "unassigned", check: { format: "pptx", status: "ok" } }];
    project.files = files;
    return r.fulfill({ json: files });
  });
  await page.route("**/api/projects/template-start-test/files/file-template", (r) => {
    Object.assign(project.files[0], r.request().postDataJSON());
    return r.fulfill({ json: project.files[0] });
  });
  await page.route("**/api/templates", (r) => {
    if (r.request().method() === "POST") {
      project.template_id = "tpl-new";
      return r.fulfill({ json: { template_id: "tpl-new", status: "queued" } });
    }
    return r.fulfill({ json: [{ template_id: "tpl-existing", name: "Фирменный.pptx", status: "succeeded" }, ...(uploaded ? [{ template_id: "tpl-new", name: "Новый.pptx", status: "queued" }] : [])] });
  });
  await page.route("**/api/templates/tpl-*", (r) => r.fulfill({ json: { template_id: r.request().url().split("/").pop(), name: "Шаблон", status: "queued" } }));
  await page.route("**/api/generations", (r) => { generations += 1; return r.fulfill({ status: 500 }); });

  await page.goto("/project?id=template-start-test");
  // Выбор шаблона — в центре пустой правой панели, в чате остаётся только приветствие.
  await expect(page.getByTestId("preview-empty")).toContainText("Выберите шаблон оформления");
  await expect(page.getByTestId("chat-greeting")).toHaveText("Опишите, какая нужна презентация, или перетащите сюда материалы. Шаблон оформления выберите справа.");
  await expect(page.getByTestId("chat-list").getByTestId("template-menu")).toHaveCount(0);
  await expect(page.locator(".editor-header").getByTestId("template-menu")).toHaveCount(0);
  // Лента прижата к низу: приветствие стоит у поля ввода, а не под шапкой.
  const list = await page.getByTestId("chat-list").boundingBox();
  const hello = await page.getByTestId("chat-list").getByTestId("msg-assistant").first().boundingBox();
  expect(list && hello && list.y + list.height - (hello.y + hello.height)).toBeLessThan(40);
  const trigger = page.getByTestId("preview-pane").getByTestId("template-menu");
  await trigger.click();
  await expect(page.getByRole("menuitem").first()).toContainText("Добавить свой");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).not.toBeVisible();
  // После клика мышью Safari не держит фокус на кнопке, поэтому возврат фокуса проверяется
  // только для открытия с клавиатуры.
  await trigger.focus();
  // Enter открывает список; стрелка сразу после него ведёт на первый пункт, даже если Mantine
  // ещё не перенёс фокус внутрь списка.
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menu")).toBeVisible();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem").first()).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).not.toBeVisible();
  await expect(trigger).toBeFocused();
  // Стрелки на закрытой кнопке открывают список и встают на первый или последний пункт.
  await page.keyboard.press("ArrowUp");
  await expect(page.getByRole("menuitem").last()).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem").first()).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByTestId("template-option-tpl-existing")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menu")).not.toBeVisible();
  await expect(page.getByTestId("template-menu")).toContainText("Фирменный");
  await expect(page.getByTestId("preview-empty")).toContainText("Здесь появится ваша презентация");
  await expect(page.getByText("Шаблон выбран. Опишите задачу презентации или добавьте материалы.")).toBeVisible();
  await expect.poll(() => project.template_id).toBe("tpl-existing");
  await page.reload();
  await expect(page.getByTestId("template-start")).toHaveCount(1);
  await expect(page.getByTestId("template-menu")).toContainText("Фирменный");

  await page.getByTestId("template-menu").click();
  await expect(page.getByTestId("template-option-tpl-existing")).toHaveAttribute("data-selected", "true");
  const chooser = page.waitForEvent("filechooser");
  await page.getByTestId("template-upload").click();
  await (await chooser).setFiles({ name: "Новый.pptx", mimeType: "application/vnd.openxmlformats-officedocument.presentationml.presentation", buffer: Buffer.from("test-template") });
  await expect.poll(() => project.template_id).toBe("tpl-new");
  await expect(page.getByTestId("template-menu")).toContainText("Новый");
  await expect(page.getByTestId("answer-template")).toHaveCount(0);
  expect(generations).toBe(0);
  await page.setViewportSize({ width: 1280, height: 800 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("empty template picker keeps upload available on a narrow screen", async ({ page }) => {
  await page.route("**/api/office/capabilities", (r) => r.fulfill({ json: { enabled: false } }));
  await page.route("**/api/projects/template-empty-test", (r) => r.fulfill({ json: {
    project_id: "template-empty-test", title: "Новая презентация", files: [], events: [], brief: {}, settings: {}, template_id: null,
  } }));
  await page.route("**/api/templates", (r) => r.fulfill({ json: [] }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/project?id=template-empty-test");
  await page.getByTestId("template-menu").click();
  await expect(page.getByRole("menuitem").first()).toContainText("Добавить свой");
  await expect(page.getByText("Пока нет шаблонов. Добавьте свой первым.")).toBeVisible();
  const bounds = await page.getByRole("menu").boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await expect(page.getByTestId("template-upload")).toBeEnabled();
});