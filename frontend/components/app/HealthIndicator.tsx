"use client";

import { Tooltip } from "@mantine/core";
import { useEffect, useState } from "react";

import { api, type HealthResponse } from "@/lib/api/client";

/**
 * Состояние сервиса и модели в шапке.
 *
 * Раньше при исправном сервисе оставалась одна точка 7×7 без подписи: формально «всё в
 * порядке», на деле — статус, которого не видно. Теперь рядом с точкой всегда стоит строка:
 * какая модель отвечает и сколько слоёв ещё на заглушках. Подробности (воркеры, Valkey,
 * рендерер, версия, провайдер) — в подсказке, чтобы шапка оставалась одной строкой.
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
            ? `ИИ · ${model}`
            : "Сервис работает";

  const lines = [
    health.status === "ok" ? "Сервис работает" : health.status === "degraded" ? "Сервис ограничен" : "Сервис недоступен",
    `Воркеры: анализ ${health.workers.analysis}, генерация ${health.workers.generation}`,
    `Valkey ${health.valkey_ok ? "ок" : "нет"} · рендерер ${health.renderer_ok ? "ок" : "нет"} · версия ${health.version}`,
    health.provider
      ? `Модель: ${llm?.model ?? "—"}${vlm?.model && vlm.model !== llm?.model ? `, зрение ${vlm.model}` : ""}${health.provider.host ? ` · ${health.provider.host.replace(/^https?:\/\//, "")}` : ""}${health.provider.probed ? "" : " · без проверки связи"}`
      : "Провайдер модели не сообщён",
    stubs.length > 0 ? `Заглушки: ${stubs.map(([k]) => k).join(", ")}` : "Все слои работают по-настоящему",
  ];

  return (
    <Tooltip multiline w={330} label={lines.join("\n")} styles={{ tooltip: { whiteSpace: "pre-line", lineHeight: 1.45 } }}>
      <span className="quiet-status" data-tone={tone} data-testid="health">
        <i />
        {text}
        {stubs.length > 0 && (
          <span className="quiet-status-note" data-testid="health-stubs">
            заглушек: {stubs.length}
          </span>
        )}
      </span>
    </Tooltip>
  );
}
