"use client";

import { Checkbox, Collapse, Group, NumberInput, SegmentedControl, Stack, Switch, Text, Tooltip } from "@mantine/core";
import { useState } from "react";

import type { CapabilitiesResponse } from "@/lib/api/client";
import { VARIANT_LABELS } from "@/lib/format";
import type { SettingsDraft } from "@/lib/state/projects";

interface Props {
  settings: SettingsDraft;
  onChange: (settings: SettingsDraft) => void;
  caps: CapabilitiesResponse | null;
}

/** Проверка настроек перед запуском: текст ошибки или null. */
export function settingsError(s: SettingsDraft): string | null {
  if (s.variants.length === 0) return "Выберите хотя бы один вариант вёрстки";
  if (s.mode === "range" && s.min > s.max) return "Минимум больше максимума";
  return null;
}

export function SettingsPanel({ settings, onChange, caps }: Props) {
  const [advanced, setAdvanced] = useState(settings.seed !== null || settings.force);
  const set = <K extends keyof SettingsDraft>(key: K, value: SettingsDraft[K]) => onChange({ ...settings, [key]: value });
  const max = caps?.limits.slide_count_max ?? 60;
  const rangeError = settings.mode === "range" && settings.min > settings.max ? "Минимум больше максимума" : null;
  const num = (v: number | string, fallback: number) => (v === "" || Number.isNaN(Number(v)) ? fallback : Number(v));

  return (
    <Stack gap="lg">
      <div>
        <Text size="sm" fw={500} mb={6}>Варианты вёрстки</Text>
        <Checkbox.Group value={settings.variants} onChange={(v) => set("variants", v)}>
          <Stack gap="xs">
            {(["compact", "balanced", "detailed"] as const).map((v) => (
              <Checkbox key={v} value={v} label={VARIANT_LABELS[v]} data-testid={`variant-${v}`} />
            ))}
          </Stack>
        </Checkbox.Group>
        <Text size="xs" c="dimmed" mt={6}>Варианты различаются плотностью подачи и одинаково соответствуют правилам шаблона.</Text>
      </div>

      <div>
        <Text size="sm" fw={500} mb={6}>Число слайдов</Text>
        <SegmentedControl fullWidth size="xs" value={settings.mode} onChange={(v) => set("mode", v as SettingsDraft["mode"])} data={[{ value: "range", label: "Диапазон" }, { value: "exact", label: "Точно" }]} mb="xs" />
        {settings.mode === "range" ? (
          <Group grow align="flex-start">
            <NumberInput label="от" min={1} max={max} value={settings.min} onChange={(v) => set("min", num(v, 1))} data-testid="slides-min" />
            <NumberInput label="до" min={1} max={max} value={settings.max} onChange={(v) => set("max", num(v, max))} data-testid="slides-max" error={rangeError} />
          </Group>
        ) : (
          <NumberInput label="ровно" min={1} max={max} value={settings.exact} onChange={(v) => set("exact", num(v, 12))} />
        )}
        <Text size="xs" c="dimmed" mt={6}>По ТЗ целевой объём 10–15 слайдов; точное число соблюдается во всех вариантах.</Text>
      </div>

      <Stack gap="sm">
        <Switch checked={settings.contextual} onChange={(e) => set("contextual", e.currentTarget.checked)} label="Контекстный аудит моделью" description="11 вопросов по каждому слайду. Без него аудит неполный, результат получит статус «требует проверки»." />
        <Tooltip label="Генерация новых изображений доступна только после этапа топ-10" disabled={caps?.features.generate_images}>
          <div>
            <Switch checked={settings.images} onChange={(e) => set("images", e.currentTarget.checked)} disabled={!caps?.features.generate_images} label="Генерировать новые изображения" description="Картинки из контент-пакета используются всегда" />
          </div>
        </Tooltip>
      </Stack>

      <div>
        <Text size="sm" c="blue" style={{ cursor: "pointer" }} onClick={() => setAdvanced((a) => !a)} role="button">
          {advanced ? "Скрыть расширенные настройки" : "Расширенные настройки"}
        </Text>
        <Collapse expanded={advanced}>
          <Stack gap="sm" pt="sm">
            <NumberInput label="Seed" description="Передаётся провайдеру; побитовая воспроизводимость не гарантируется" value={settings.seed ?? ""} onChange={(v) => set("seed", v === "" ? null : Number(v))} />
            <Switch checked={settings.force} onChange={(e) => set("force", e.currentTarget.checked)} label="Явная перегенерация" description="Не использовать кэш ответов модели" />
          </Stack>
        </Collapse>
      </div>
    </Stack>
  );
}
