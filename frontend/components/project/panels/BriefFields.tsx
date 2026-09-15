"use client";

import { Group, Select, Stack, TagsInput, TextInput, Textarea } from "@mantine/core";

import type { BriefDraft } from "@/lib/state/projects";

export const PURPOSE_OPTIONS = [
  { value: "feature", label: "Фича" },
  { value: "product", label: "Продукт" },
  { value: "project", label: "Проект" },
  { value: "initiative", label: "Инициатива" },
  { value: "report", label: "Отчёт" },
];

export const PURPOSE_LABELS: Record<string, string> = Object.fromEntries(PURPOSE_OPTIONS.map((o) => [o.value, o.label]));

/** Поля брифа. Обычно заполняются из сообщения в чате; форма нужна, чтобы поправить понятое. */
export function BriefFields({ brief, onChange }: { brief: BriefDraft; onChange: (b: BriefDraft) => void }) {
  const set = <K extends keyof BriefDraft>(key: K, value: BriefDraft[K]) => onChange({ ...brief, [key]: value });
  return (
    <Stack gap="xs">
      <TextInput label="Название" placeholder="Запуск сервиса умных уведомлений" value={brief.title} onChange={(e) => set("title", e.currentTarget.value)} data-testid="brief-title" />
      <Group grow>
        <Select label="Назначение" data={PURPOSE_OPTIONS} value={brief.purpose || null} placeholder="Не выбрано" onChange={(v) => set("purpose", v ?? "")} allowDeselect={false} data-testid="brief-purpose" />
        <Select label="Язык" data={[{ value: "ru", label: "Русский" }, { value: "en", label: "English" }]} value={brief.language} onChange={(v) => set("language", v ?? "ru")} allowDeselect={false} />
      </Group>
      <TextInput label="Аудитория" placeholder="руководители продуктовых направлений" value={brief.audience} onChange={(e) => set("audience", e.currentTarget.value)} />
      <Textarea label="Цель" placeholder="получить одобрение на пилот" autosize minRows={1} value={brief.goal} onChange={(e) => set("goal", e.currentTarget.value)} />
      <TextInput label="Тон" placeholder="деловой, уверенный" value={brief.tone} onChange={(e) => set("tone", e.currentTarget.value)} />
      <TagsInput label="Обязательно включить" placeholder="метрики пилота, план на квартал" value={brief.must_include} onChange={(v) => set("must_include", v)} />
      <TagsInput label="Избегать" placeholder="технические детали инфраструктуры" value={brief.avoid} onChange={(v) => set("avoid", v)} />
    </Stack>
  );
}
