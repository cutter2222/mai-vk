"use client";

import { Badge, Tooltip } from "@mantine/core";
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

  if (health === null) return <Badge variant="dot" color="gray">Проверка сервиса</Badge>;
  if (health === "error") return <Badge variant="dot" color="red" data-testid="health">Сервис недоступен</Badge>;
  const color = health.status === "ok" ? "green" : health.status === "degraded" ? "yellow" : "red";
  const label = `Воркеры: анализ ${health.workers.analysis}, генерация ${health.workers.generation}; Valkey ${health.valkey_ok ? "ок" : "нет"}; рендерер ${health.renderer_ok ? "ок" : "нет"}; версия ${health.version}`;
  return (
    <Tooltip label={label} withArrow>
      <Badge variant="dot" color={color} data-testid="health">
        {health.status === "ok" ? "Сервис работает" : health.status === "degraded" ? "Сервис ограничен" : "Сервис недоступен"}
      </Badge>
    </Tooltip>
  );
}
