"use client";

import { Tooltip } from "@mantine/core";
import { useEffect, useState } from "react";

import { api, type HealthResponse } from "@/lib/api/client";

/**
 * Момент сборки из тега образа: deploy.sh складывает тег как `COMMIT[-dirty]-YYYYMMDD-HHMMSS`
 * (UTC). Достаём хвост и возвращаем Date; если тега нет или он нераспознан — null.
 */
function buildTimeFromTag(tag?: string | null): Date | null {
  const m = tag?.match(/(\d{8})-(\d{6})$/);
  if (!m) return null;
  const date = new Date(
    Date.UTC(+m[1].slice(0, 4), +m[1].slice(4, 6) - 1, +m[1].slice(6, 8), +m[2].slice(0, 2), +m[2].slice(2, 4)),
  );
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Точная локальная дата и время для человека: «29.09.2026, 14:20» — в часовом поясе зрителя. */
function formatBuildTime(date: Date): string {
  return date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/**
 * Состояние сервиса и модели в шапке.
 *
 * Раньше при исправном сервисе оставалась одна точка 7×7 без подписи: формально «всё в
 * порядке», на деле — статус, которого не видно. Теперь рядом с точкой всегда стоит строка:
 * какая модель отвечает и сколько слоёв ещё на заглушках. Подробности (воркеры, Valkey,
 * рендерер, версия, провайдер) — в подсказке, чтобы шапка оставалась одной строкой.
 *
 * Время последней сборки видно сразу (а не только в подсказке): комиссия должна видеть,
 * когда на сервере появился именно этот бильд, — особенно в день дедлайна.
 */
export function HealthIndicator() {
  const [health, setHealth] = useState<HealthResponse | null | "error">(null);
  const mock = process.env.NEXT_PUBLIC_API_MODE === "mock";

  useEffect(() => {
    if (mock) return;
    let alive = true;
    const load = () =>
      api
        .health()
        .then((h) => alive && setHealth(h))
        .catch(() => alive && setHealth("error"));
    void load();
    const id = setInterval(load, 30000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [mock]);

  if (mock) {
    return (
      <Tooltip label="Интерфейс работает на данных-заглушках: настоящий API не вызывается">
        <span className="quiet-status" data-tone="warn" data-testid="mode-badge">
          <i />
          Режим заглушек
        </span>
      </Tooltip>
    );
  }
  if (health === null) {
    return (
      <span className="quiet-status" data-testid="health-loading">
        <i />
        Проверяем сервис
      </span>
    );
  }
  if (health === "error") {
    return (
      <span className="quiet-status" data-tone="bad" data-testid="health">
        <i />
        Сервис недоступен
      </span>
    );
  }

  const llm = health.provider?.roles?.llm;
  const vlm = health.provider?.roles?.vlm;
  const stubs = Object.entries(health.execution_mode?.layers ?? {}).filter(([, v]) => v === "stub");
  // Модель важнее номера версии: она и есть «кто это собирает».
  const model = health.provider?.configured ? (llm?.model ?? health.provider.name) : null;
  // Провайдера в ответе может не быть вовсе (заглушечный API в тестах) — это не то же самое,
  // что «провайдер есть, но не настроен»: во втором случае предупреждаем.
  const modelUnknown = health.provider !== undefined && !health.provider.configured;
  const tone = health.status === "ok" ? (modelUnknown ? "warn" : "ok") : health.status === "degraded" ? "warn" : "bad";
  const text =
    health.status === "down"
      ? "Сервис недоступен"
      : health.status === "degraded"
        ? `Сервис ограничен${model ? ` · ${model}` : ""}`
        : modelUnknown
          ? "Модель не настроена"
          : model
            ? model
            : "Сервис работает";

  const buildTime = buildTimeFromTag(health.release?.image_tag);
  const lines = [
    health.status === "ok" ? "Сервис работает" : health.status === "degraded" ? "Сервис ограничен" : "Сервис недоступен",
    `Воркеры: анализ ${health.workers.analysis}, генерация ${health.workers.generation}`,
    `Valkey ${health.valkey_ok ? "ок" : "нет"} · рендерер ${health.renderer_ok ? "ок" : "нет"} · версия ${health.version}`,
    buildTime ? `Сборка: ${formatBuildTime(buildTime)}${health.release?.commit ? ` · ${health.release.commit}` : ""}` : null,
    health.provider
      ? `Модель: ${llm?.model ?? "—"}${vlm?.model && vlm.model !== llm?.model ? `, зрение ${vlm.model}` : ""}${health.provider.host ? ` · ${health.provider.host.replace(/^https?:\/\//, "")}` : ""}${health.provider.probed ? "" : " · без проверки связи"}`
      : "Провайдер модели не сообщён",
    stubs.length > 0 ? `Заглушки: ${stubs.map(([k]) => k).join(", ")}` : "Все слои работают по-настоящему",
  ].filter((line): line is string => line !== null);

  return (
    <Tooltip multiline w={330} label={lines.join("\n")} styles={{ tooltip: { whiteSpace: "pre-line", lineHeight: 1.45 } }}>
      <span className="quiet-status" data-tone={tone} data-testid="health">
        <i />
        {text}
        {buildTime && (
          <span className="quiet-status-note" data-testid="health-build-time">
            обновлено {formatBuildTime(buildTime)}
          </span>
        )}
        {stubs.length > 0 && (
          <span className="quiet-status-note" data-testid="health-stubs">
            заглушек: {stubs.length}
          </span>
        )}
      </span>
    </Tooltip>
  );
}
