import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

import { attach, collectConsoleErrors, createProject, sendMaterialsAndBrief, sendMessage, speedUp, startGeneration, uploadTemplate, waitForAllVariantsDone } from "./helpers";

const SHOTS = process.env.SHOT_DIR;
const shot = async (page: Page, name: string) => {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });
};

test.describe("сквозной сценарий в чате на заглушках", () => {
  test("шаблон → материалы и задача → генерация → варианты → аудит → исправление → ревизия → скачивание", async ({ page, browserName }) => {
    await speedUp(page, 8);
    const errors = collectConsoleErrors(page);

    await createProject(page);
    await expect(page.getByTestId("health")).toContainText("Сервис работает");
    await expect(page.getByTestId("chat-intro")).toBeVisible();
    await expect(page.getByTestId("preview-empty")).toBeVisible();

    await uploadTemplate(page);
    await expect(page.getByTestId("template-analyzing")).toBeVisible();
    // Текущий шаблон виден в шапке, в списке он отмечен галочкой
    await expect(page.getByTestId("template-menu")).toContainText("Корпоративный шаблон");
    await page.getByTestId("template-menu").click();
    await expect(page.locator('[data-testid^="template-option-"]').filter({ hasText: "Корпоративный шаблон" })).toBeVisible();
    await page.keyboard.press("Escape");

    await sendMaterialsAndBrief(page);
    await expect(page.getByTestId("import-summary").last()).toContainText("фактов");
    // Бриф понят из фразы: название проекта и поля карточки
    await expect(page.getByTestId("project-title")).toHaveValue("Запуск сервиса умных уведомлений");
    await expect(page.getByTestId("brief-card").last()).toContainText("Продукт");
    await expect(page.getByTestId("brief-card").last()).toContainText("руководителей");
    await expect(page.getByTestId("generate").last()).toBeEnabled({ timeout: 15000 });

    // Профиль шаблона в карточке и образцы справа после анализа
    await expect(page.getByTestId("template-profile").last()).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("thumb-strip")).toBeVisible();
    await shot(page, "chat-ready");

    // Параметры: проверка границ диапазона слайдов
    await page.getByTestId("edit-brief").last().click();
    await page.getByRole("tab", { name: "Параметры генерации" }).click();
    await page.getByTestId("slides-min").fill("20");
    await expect(page.getByText("Минимум больше максимума").first()).toBeVisible();
    await page.getByTestId("slides-min").fill("10");
    await page.getByTestId("brief-modal-done").click();

    const jobId = await startGeneration(page);
    expect(jobId).toMatch(/^job_/);
    await expect(page.getByTestId("job-card")).toBeVisible();
    await expect(page.getByTestId("execution-mode").last()).toContainText("Заглушки");
    await expect(page.getByTestId("preview-progress")).toBeVisible();

    // Файлы появляются раньше аудита
    await expect(page.getByTestId("download-menu")).toBeEnabled({ timeout: 40000 });
    await shot(page, "chat-running");

    // Сравнение вариантов рядом
    await page.getByTestId("layout-switch").getByText("Сравнить").click();
    await expect(page.getByTestId("variants-grid")).toBeVisible();
    await expect(page.locator('[data-testid^="variant-card-"]')).toHaveCount(3);
    await page.getByTestId("layout-switch").getByText("Один вариант").click();

    await waitForAllVariantsDone(page);
    await expect(page.getByTestId("audit-card")).toBeVisible({ timeout: 15000 });
    await shot(page, "chat-done");

    // Скачивание PPTX: настоящий zip-пакет, а не JSON
    if (browserName === "chromium") {
      await page.getByTestId("download-menu").click();
      const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId("dl-pptx").click()]);
      expect(download.suggestedFilename()).toMatch(/\.pptx$/);
      const path = await download.path();
      expect(path).toBeTruthy();
      const head = readFileSync(path as string).subarray(0, 2).toString("latin1");
      expect(head).toBe("PK");
      await page.keyboard.press("Escape");
    }

    // Аудит: карточка в чате открывает панель рядом со слайдом на варианте с находками
    await page.getByTestId("open-audit").click();
    await expect(page.getByTestId("audit-drawer")).toBeVisible();
    await expect(page.getByTestId("audit-coverage")).toContainText("неполное");
    await page.getByTestId("issue-iss_1").click();
    await expect(page.getByTestId("issue-box-iss_1")).toBeVisible();
    await page.getByTestId("toggle-how-built").click();
    await expect(page.getByTestId("how-built")).toBeVisible();
    await shot(page, "chat-audit");

    await page.getByTestId("issue-check-iss_1").check();
    await page.getByTestId("repair").click();
    await expect(page.getByText("Исправления применены")).toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId("revisions-panel")).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("audit-panel")).toContainText("ревизия 2");
    await expect(page.getByTestId("issue-iss_1")).toHaveCount(0);
    await shot(page, "chat-revision");

    // Лента миниатюр скрыта, пока открыт аудит; закрываем его и листаем клавиатурой
    await page.getByTestId("toggle-audit").click();
    await expect(page.getByTestId("audit-drawer")).toHaveCount(0);
    await page.locator('[data-testid="thumb-strip"] button').first().click();
    await expect(page.getByTestId("slide-counter")).toHaveText(/Слайд 1 из/);
    await page.keyboard.press("ArrowDown");
    await expect(page.getByTestId("slide-counter")).toHaveText(/Слайд 2 из/);

    // Файлы проекта: загруженное и собранное
    await page.getByTestId("tab-files").click();
    await expect(page.getByTestId("files-panel")).toContainText("Корпоративный шаблон.pptx");
    await expect(page.getByTestId("files-panel")).toContainText("metrics.xlsx");
    await expect(page.getByTestId("files-panel")).toContainText("Собрано сервисом");
    await shot(page, "chat-files");

    // Проект с готовой презентацией виден в сетке
    await page.getByTestId("back-home").click();
    await expect(page.locator('[data-testid^="project-card-"]').first()).toContainText("Запуск сервиса умных уведомлений");
    await shot(page, "home-grid");

    expect(errors, errors.join("\n")).toEqual([]);
  });

  test("чат переживает перезагрузку, проект открывается из сетки", async ({ page }) => {
    await speedUp(page, 8);
    const projectId = await createProject(page);
    await uploadTemplate(page);
    await sendMaterialsAndBrief(page);
    const jobId = await startGeneration(page);
    await page.reload();
    await expect(page.getByTestId("template-card")).toBeVisible();
    await expect(page.getByTestId("progress-panel").last()).toContainText(jobId, { timeout: 15000 });
    await page.goto("/");
    const card = page.getByTestId(`project-card-${projectId}`);
    await expect(card).toBeVisible();
    await card.click();
    await page.waitForURL(new RegExp(`/project\\?id=${projectId}`));
    await expect(page.getByTestId("progress-panel").last()).toContainText(jobId);
  });

  test("непонятное сообщение, уточнение назначения, PPTX как материал и удаление файла", async ({ page }) => {
    await createProject(page);
    await sendMessage(page, "Привет!");
    await expect(page.getByTestId("msg-assistant").last()).toContainText("Не нашёл");

    // Назначение не сказано — чат спрашивает кнопками
    await sendMessage(page, "Сделай слайды про итоги квартала для команды");
    await expect(page.getByTestId("brief-card").last()).toContainText("Уточните назначение");
    await page.getByTestId("purpose-report").click();
    await expect(page.getByTestId("brief-card").last()).toContainText("Отчёт");

    // PPTX как материал, а не шаблон
    await attach(page, [{ name: "Старая презентация.pptx", mimeType: "application/vnd.openxmlformats-officedocument.presentationml.presentation", buffer: Buffer.from("PK") }]);
    await sendMessage(page);
    await page.getByTestId("answer-material").last().click();
    await expect(page.getByTestId("import-summary").last()).toBeVisible({ timeout: 15000 });

    // Файлы проекта: сгруппированы по типу, добавляются прямо в панель, удаление материала переимпортирует содержание
    await page.getByTestId("tab-files").click();
    await expect(page.getByTestId("files-panel")).toContainText("Презентации");
    const [chooser] = await Promise.all([page.waitForEvent("filechooser"), page.getByTestId("files-add").click()]);
    await chooser.setFiles([{ name: "brief.docx", mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", buffer: Buffer.from("mock") }]);
    await expect(page.getByTestId("files-panel")).toContainText("Документы");
    await expect(page.getByTestId("files-panel")).toContainText("brief.docx");
    const row = page.locator('[data-testid^="file-"]').filter({ hasText: "Старая презентация.pptx" }).first();
    await row.locator('[data-testid^="file-remove-"]').click();
    await expect(page.getByTestId("files-panel")).not.toContainText("Старая презентация.pptx");
    await page.getByTestId("tab-chat").click();
    await expect(page.getByTestId("msg-assistant").last()).toContainText("удалён");
  });

  test("сетка проектов: переименование и удаление", async ({ page }) => {
    const projectId = await createProject(page);
    await page.getByTestId("back-home").click();
    await expect(page.getByTestId("projects-grid")).toBeVisible();
    await page.getByTestId(`project-menu-${projectId}`).click();
    await page.getByText("Переименовать").click();
    await page.getByTestId("rename-input").fill("Квартальный отчёт");
    await page.getByRole("button", { name: "Сохранить" }).click();
    await expect(page.getByTestId(`project-card-${projectId}`)).toContainText("Квартальный отчёт");
    await page.getByTestId(`project-menu-${projectId}`).click();
    await page.getByTestId(`project-delete-${projectId}`).click();
    await page.getByTestId("confirm-delete").click();
    await expect(page.getByTestId(`project-card-${projectId}`)).toHaveCount(0);
    await expect(page.getByTestId("projects-empty")).toBeVisible();
  });

  test("неизвестное задание, прежняя ссылка /workspace и /project без параметра", async ({ page }) => {
    await page.goto("/workspace?job=job_unknown");
    await page.waitForURL(/\/project\?id=/);
    await expect(page.getByRole("heading", { name: "Задание не найдено" })).toBeVisible();
    await page.goto("/project");
    await expect(page.getByTestId("project-empty")).toBeVisible();
    await page.goto("/project?id=prj_missing");
    await expect(page.getByTestId("project-empty")).toBeVisible();
  });

  test("частичная ошибка варианта и повтор", async ({ page }) => {
    await speedUp(page, 8);
    await createProject(page);
    await uploadTemplate(page, "Шаблон fail.pptx");
    await sendMaterialsAndBrief(page);
    const jobId = await startGeneration(page);
    await expect(page.getByTestId("variant-progress-detailed").last().locator('[data-testid="status-failed"]')).toBeVisible({ timeout: 40000 });
    await waitForAllVariantsDone(page);
    await expect(page.getByTestId("progress-panel").last()).toContainText("частичный результат");
    await expect(page.getByTestId("download-menu")).toBeEnabled();
    await page.getByTestId("retry").click();
    await expect(page.getByTestId("progress-panel").last()).not.toContainText(jobId, { timeout: 15000 });
    await expect(page.getByTestId("progress-panel").last()).toContainText(/job_/);
  });

  test("отмена задания", async ({ page }) => {
    await speedUp(page, 2);
    await createProject(page);
    await uploadTemplate(page);
    await sendMaterialsAndBrief(page);
    await startGeneration(page);
    await page.getByTestId("cancel").click();
    await expect(page.getByTestId("progress-panel").last().locator('[data-testid="status-canceled"]')).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("retry")).toBeVisible();
  });

  test("конфликт ревизии при исправлении по устаревшему отчёту", async ({ page }) => {
    await speedUp(page, 8);
    await createProject(page);
    await uploadTemplate(page);
    await sendMaterialsAndBrief(page);
    const jobId = await startGeneration(page);
    await waitForAllVariantsDone(page);

    // Состояние заглушки живёт в памяти вкладки, поэтому устаревание имитируем запросом с неверной ревизией.
    const response = await page.evaluate(async (id) => {
      const r = await fetch(`/api/generations/${id}/variants/balanced/repairs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_revision: 99, issue_ids: ["iss_1"] }),
      });
      return { status: r.status, body: (await r.json()) as { error: { code: string } } };
    }, jobId);
    expect(response.status).toBe(409);
    expect(response.body.error.code).toBe("revision_stale");
  });
});
