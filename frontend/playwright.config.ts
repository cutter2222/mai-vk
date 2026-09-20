import { defineConfig, devices } from "@playwright/test";

/**
 * Сквозные тесты в режиме заглушек против статического экспорта (out/), а не next dev:
 * так проверяется production-сборка и работа query-параметров на статическом сервере.
 * Скорость имитации заданий ускорена через localStorage (mock_speed).
 * PLAYWRIGHT_BASE_URL переключает тесты на уже поднятый стек (docker compose локально
 * или адрес сервера); локальный сервер out/ тогда не запускается.
 */
const externalBaseURL = process.env.PLAYWRIGHT_BASE_URL;
const VIEWPORT = { width: 1440, height: 900 };

export default defineConfig({
  testDir: "./tests",
  // Настоящий анализ шаблона на сервере (рендер + модель) занимает десятки секунд для нового
  // файла, планы трёх вариантов моделью при трёх параллельных заданиях — минуты (helpers.WAIT).
  timeout: externalBaseURL ? 600_000 : 120_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: externalBaseURL ?? "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: VIEWPORT,
  },
  webServer: externalBaseURL
    ? undefined
    : {
        command: "pnpm exec serve out -l 4173 --no-clipboard",
        url: "http://127.0.0.1:4173",
        reuseExistingServer: !process.env.CI,
        timeout: 30_000,
      },
  // Профили устройств несут свой viewport (1280×720) и перекрывали общий: на такой ширине
  // лента миниатюр при открытой панели свойств убирается совсем, и перестановка слайдов
  // в редакторе недоступна. Ширина задаётся явно в каждом проекте.
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: VIEWPORT } },
    { name: "firefox", use: { ...devices["Desktop Firefox"], viewport: VIEWPORT } },
    { name: "webkit", use: { ...devices["Desktop Safari"], viewport: VIEWPORT } },
  ],
});
