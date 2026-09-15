"use client";

import { Tooltip } from "@mantine/core";
import { useEffect, useState } from "react";

import { api, type HealthResponse } from "@/lib/api/client";

export function HealthIndicator() {
  const [health, setHealth] = useState<HealthResponse | null | "error">(null);

  useEffect(() => {
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
  }, []);

  if (health === null) return <span className="quiet-status"><i />Проверка сервиса</span>;
  if (health === "error") return <span className="quiet-status" data-tone="bad" data-testid="health"><i />Сервис недоступен</span>;
  const tone = health.status === "ok" ? "ok" : health.status === "degraded" ? "warn" : "bad";
  const label = `Воркеры: анализ ${health.workers.analysis}, генерация ${health.workers.generation}; Valkey ${health.valkey_ok ? "ок" : "нет"}; рендерер ${health.renderer_ok ? "ок" : "нет"}; версия ${health.version}`;
  return (
    <Tooltip label={label}>
      <span className="quiet-status" data-tone={tone} data-testid="health">
        <i />
        {health.status === "ok" ? "Сервис работает" : health.status === "degraded" ? "Сервис ограничен" : "Сервис недоступен"}
      </span>
    </Tooltip>
  );
}
