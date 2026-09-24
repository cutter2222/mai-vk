import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, test } from "@playwright/test";

import { cleanupProjects, createProject } from "./helpers";

/**
 * Голосовой ввод в чате на фальшивом микрофоне Chromium: браузер «слышит» запись с тремя
 * фразами и паузами между ними, режет фразы сам и отправляет их по одной; распознавание — в
 * заглушке (MSW отвечает строками по очереди, черновик недоговорённой фразы — её первой
 * половиной). Черновик виден в поле, пока фраза звучит, итог его заменяет; текст остаётся
 * после выключения записи и уходит обычным сообщением.
 */

/** Три «фразы» (тон с гармониками и слоговой модуляцией) через секунду тишины, запись по кругу. */
function fakeSpeechWav(): string {
  const rate = 16000;
  const parts: number[] = [];
  const noise = (seconds: number) => {
    for (let i = 0; i < seconds * rate; i++) parts.push((Math.random() - 0.5) * 0.002);
  };
  const phrase = (seconds: number, base: number) => {
    for (let i = 0; i < seconds * rate; i++) {
      const t = i / rate;
      const syllables = 0.55 + 0.45 * Math.sin(2 * Math.PI * 4 * t);
      const voice = Math.sin(2 * Math.PI * base * t) + 0.5 * Math.sin(2 * Math.PI * 2 * base * t) + 0.25 * Math.sin(2 * Math.PI * 3 * base * t);
      parts.push(0.25 * syllables * voice + (Math.random() - 0.5) * 0.002);
    }
  };
  noise(0.8);
  phrase(1.3, 180);
  noise(1.1);
  phrase(1.1, 210);
  noise(1.1);
  phrase(1.2, 160);
  noise(2.5);
  const data = Buffer.alloc(44 + parts.length * 2);
  data.write("RIFF", 0);
  data.writeUInt32LE(36 + parts.length * 2, 4);
  data.write("WAVE", 8);
  data.write("fmt ", 12);
  data.writeUInt32LE(16, 16);
  data.writeUInt16LE(1, 20);
  data.writeUInt16LE(1, 22);
  data.writeUInt32LE(rate, 24);
  data.writeUInt32LE(rate * 2, 28);
  data.writeUInt16LE(2, 32);
  data.writeUInt16LE(16, 34);
  data.write("data", 36);
  data.writeUInt32LE(parts.length * 2, 40);
  parts.forEach((v, i) => data.writeInt16LE(Math.round(Math.max(-1, Math.min(1, v)) * 32767), 44 + i * 2));
  const file = path.join(mkdtempSync(path.join(tmpdir(), "dictation-")), "speech.wav");
  writeFileSync(file, data);
  return file;
}

test.use({
  launchOptions: {
    args: [
      "--use-fake-device-for-media-stream",
      "--use-fake-ui-for-media-stream",
      `--use-file-for-fake-audio-capture=${fakeSpeechWav()}`,
    ],
  },
});
test.afterEach(async ({ page }) => cleanupProjects(page));

test("надиктованные фразы появляются в поле, остаются после выключения и уходят сообщением", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "фальшивый микрофон — только в Chromium");
  await page.addInitScript(() => window.localStorage.setItem("mock_speech_ms", "250"));
  await createProject(page);
  const mic = page.getByTestId("chat-mic");
  const input = page.getByTestId("chat-input");
  await expect(mic).toBeVisible();
  await expect(mic).toHaveAttribute("aria-label", "Надиктовать (русский)");

  await mic.click();
  await expect(mic).toHaveAttribute("data-recording", "true");
  // Кольцо вокруг кнопки отвечает на голос, «…» — пока фраза звучит.
  await expect.poll(() => mic.evaluate((el) => Number(el.style.getPropertyValue("--mic-level") || 0)), { timeout: 20_000 }).toBeGreaterThan(0.3);
  await expect(page.getByTestId("chat-mic-pending")).toBeVisible();
  // Черновик фразы — в поле, пока она ещё звучит; править поле можно после итога.
  await expect(input).toHaveValue("Сделай заголовок", { timeout: 20_000 });
  await expect(input).toHaveAttribute("readonly", "");
  // Итог фразы заменяет черновик, пока запись ещё идёт.
  await expect(input).toHaveValue("Сделай заголовок короче.", { timeout: 20_000 });
  await expect(mic).toHaveAttribute("data-recording", "true");
  // Следующая дописывается через пробел.
  await expect(input).toHaveValue(/^Сделай заголовок короче\. Добавь вывод на последний слайд\./, { timeout: 20_000 });

  await mic.click();
  await expect(mic).not.toHaveAttribute("data-recording");
  const dictated = await input.inputValue();
  expect(dictated.startsWith("Сделай заголовок короче. Добавь вывод на последний слайд.")).toBe(true);
  await page.waitForTimeout(1500);
  // После выключения поле не меняется само: текст можно поправить и отправить.
  await expect(input).toHaveValue(dictated);
  await input.fill(`${dictated} Спасибо.`);
  await page.getByTestId("chat-send").click();
  await expect(page.getByTestId("chat-list").locator(".chat-bubble").last()).toHaveText(`${dictated} Спасибо.`);
  await expect(input).toHaveValue("");
});

test("Esc выключает запись, а без голосового ввода кнопки нет", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "фальшивый микрофон — только в Chromium");
  await createProject(page);
  const mic = page.getByTestId("chat-mic");
  await mic.click();
  await expect(mic).toHaveAttribute("data-recording", "true");
  await page.keyboard.press("Escape");
  await expect(mic).not.toHaveAttribute("data-recording");

  await page.evaluate(() => window.localStorage.setItem("mock_speech", "off"));
  await page.reload();
  await expect(page.getByTestId("chat-input")).toBeVisible();
  await expect(page.getByTestId("chat-mic")).toHaveCount(0);
});
