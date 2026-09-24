import { expect, test } from "@playwright/test";

import { cleanupProjects, createProject, projectIdAfterAction } from "./helpers";

/**
 * Голосовой ввод на живом стеке с настоящей моделью: фальшивый микрофон Chromium проигрывает
 * запись речи один раз, интерфейс режет фразы, сервис распознаёт их GigaAM. Спека записывает
 * каждое изменение поля: когда появились первые слова (черновик, пока первая фраза звучит) и
 * как быстро после конца каждой фразы (DICTATION_ENDS, секунды от начала файла) поле
 * обновилось. Человека у микрофона она не заменяет.
 *   DICTATION_FILE=speech.wav DICTATION_ENDS=6.24,11.68,15.49 PLAYWRIGHT_BASE_URL=https://izbox.ru \
 *     playwright test chat-dictation-live --project=chromium
 */
const FILE = process.env.DICTATION_FILE;
const ENDS = (process.env.DICTATION_ENDS ?? "").split(",").filter(Boolean).map(Number);

test.use({
  trace: "off",
  launchOptions: {
    args: FILE ? ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", `--use-file-for-fake-audio-capture=${FILE}%noloop`] : [],
  },
});
test.afterEach(async ({ page }) => cleanupProjects(page));

test("надиктованная речь появляется в поле по фразам и уходит сообщением", async ({ page, browserName }, info) => {
  test.skip(!process.env.PLAYWRIGHT_BASE_URL || !FILE || browserName !== "chromium", "нужны PLAYWRIGHT_BASE_URL, DICTATION_FILE и Chromium");
  test.setTimeout(180_000);
  const note = (type: string, description: string) => { info.annotations.push({ type, description }); console.log(`${type}: ${description}`); };
  await createProject(page);
  const mic = page.getByTestId("chat-mic");
  const input = page.getByTestId("chat-input");
  await expect(mic).toBeVisible({ timeout: 30_000 });
  const clicked = Date.now();
  await mic.click();
  await expect(mic).toHaveAttribute("data-recording", "true");
  const changes: Array<{ ms: number; value: string }> = [];
  let last = "";
  // Слушаем до конца записи и ещё 4 с — на итог последней фразы.
  const until = ((ENDS.length ? Math.max(...ENDS) : 20) + 4) * 1000;
  while (Date.now() - clicked < until) {
    const value = await input.inputValue();
    if (value !== last) {
      changes.push({ ms: Date.now() - clicked, value });
      last = value;
    }
    await page.waitForTimeout(50);
  }
  changes.forEach((c, i) => note(`change_${i + 1}`, `${(c.ms / 1000).toFixed(2)} с: «${c.value}»`));
  ENDS.forEach((end, i) => {
    const next = changes.find((c) => c.ms > end * 1000);
    if (next) note(`after_phrase_${i + 1}`, `поле обновилось через ${((next.ms / 1000) - end).toFixed(2)} с после конца фразы`);
  });
  expect(changes.length).toBeGreaterThan(0);
  // Первые слова — пока первая фраза ещё звучит, а не после паузы.
  if (ENDS.length) expect(changes[0].ms).toBeLessThan(ENDS[0] * 1000);
  // Знаки препинания и слова по-русски — модель e2e.
  expect(last).toMatch(/[а-яё].*[,.!?]/i);
  await expect(mic).toHaveAttribute("data-recording", "true");
  await mic.click();
  await expect(mic).not.toHaveAttribute("data-recording");
  await page.waitForTimeout(1000);
  await expect(input).toHaveValue(last);
  await page.getByTestId("chat-send").click();
  // Черновик становится проектом с первым сообщением: запоминаем его, чтобы убрать после теста.
  await projectIdAfterAction(page);
  await expect(page.getByTestId("chat-list").locator(".chat-bubble").first()).toHaveText(last);
});
