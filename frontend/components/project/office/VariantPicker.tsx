"use client";

import { Loader, Menu, Text, UnstyledButton } from "@mantine/core";
import { IconCheck, IconChevronDown, IconLayoutList, IconLayoutRows, IconListDetails, IconPlus } from "@tabler/icons-react";
import { useState, type ReactNode } from "react";

import { VARIANT_LABELS } from "@/lib/format";
import styles from "../TemplatePicker.module.css";

/** Что отличает вариант: одна строка под названием в списке. */
const VARIANT_HINTS: Record<string, string> = {
  compact: "Меньше текста, крупные тезисы и визуал",
  balanced: "Текст и визуал поровну",
  detailed: "Больше пояснений и деталей",
};

const VARIANT_ICONS: Record<string, ReactNode> = {
  compact: <IconLayoutRows size={18} stroke={1.6} />,
  balanced: <IconLayoutList size={18} stroke={1.6} />,
  detailed: <IconListDetails size={18} stroke={1.6} />,
};

export interface VariantOption {
  id: string;
  /** Вариант собран: его PPTX можно открыть. */
  ready: boolean;
  failed: boolean;
  /** Вариант в этом задании не собирался: его можно собрать. */
  absent?: boolean;
}

/** Три типа вёрстки — порядок в списке всегда один. */
export const VARIANT_ORDER = ["compact", "balanced", "detailed"];

/**
 * Выбор варианта вёрстки — выпадающий список рядом с выбором шаблона и в том же виде
 * (29.09.2026): сегментный переключатель на три длинных названия был шире шапки и выглядел
 * чужим рядом с кнопкой шаблона. Несобранный вариант виден с загрузкой и не выбирается.
 */
export function VariantPicker({ options, value, onChange, onAdd, disabled }: {
  options: VariantOption[];
  value: string;
  onChange: (id: string) => void;
  /** Собрать тип, которого в задании нет. */
  onAdd?: (id: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const current = options.find((o) => o.id === value);
  const building = options.some((o) => !o.ready && !o.failed && !o.absent);
  return (
    <div className={styles.root}>
      <Menu withinPortal position="bottom-end" shadow="md" width={300} opened={open} onChange={setOpen}
        styles={{ dropdown: { maxWidth: "calc(100vw - 24px)" } }}>
        <Menu.Target>
          <UnstyledButton className={styles.trigger} disabled={disabled} aria-label="Вариант презентации"
            title="Вариант вёрстки" data-testid="office-variants" data-value={value}>
            <span className={styles.preview} aria-hidden>{VARIANT_ICONS[value] ?? <IconLayoutList size={18} stroke={1.6} />}</span>
            <span className={styles.copy}>
              <span className={styles.name}>{current ? VARIANT_LABELS[current.id] ?? current.id : "Вариант"}</span>
            </span>
            {building && <Loader size={12} aria-label="варианты ещё собираются" />}
            <IconChevronDown size={16} className={styles.chevron} aria-hidden />
          </UnstyledButton>
        </Menu.Target>
        <Menu.Dropdown className={styles.dropdown}>
          <Menu.Label>Вариант вёрстки</Menu.Label>
          {options.map((o) => {
            const state = o.ready ? "ready" : o.failed ? "failed" : o.absent ? "absent" : "building";
            const label = VARIANT_LABELS[o.id] ?? o.id;
            return (
              <Menu.Item
                key={o.id}
                className={styles.option}
                data-selected={o.id === value || undefined}
                disabled={!o.ready && !(o.absent && onAdd)}
                onClick={() => {
                  if (o.ready && o.id !== value) onChange(o.id);
                  else if (o.absent) onAdd?.(o.id);
                }}
                leftSection={<span className={styles.addIcon}>{VARIANT_ICONS[o.id] ?? <IconLayoutList size={18} stroke={1.6} />}</span>}
                rightSection={o.id === value ? <IconCheck size={14} /> : state === "building" ? <Loader size={12} aria-label="собирается" /> : state === "absent" ? <IconPlus size={14} /> : undefined}
                data-testid={`office-variant-${o.id}`}
                data-state={state}
                aria-checked={o.id === value}
                role="menuitemradio"
              >
                <Text size="sm" fw={500} td={o.failed ? "line-through" : undefined}>{label}</Text>
                <Text size="xs" c="dimmed">{o.failed ? "не собрался" : state === "building" ? "собирается…" : state === "absent" ? `Собрать · ${(VARIANT_HINTS[o.id] ?? "").toLowerCase()}` : VARIANT_HINTS[o.id] ?? ""}</Text>
              </Menu.Item>
            );
          })}
        </Menu.Dropdown>
      </Menu>
    </div>
  );
}
