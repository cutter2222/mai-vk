"use client";

import { Button, Group, Modal, Stack, Text, Title } from "@mantine/core";

import { SlideImage } from "@/components/common/SlideImage";

/** Блок карточки шаблона: заголовок, необязательная строка справа и содержимое. */
export function Section({ title, aside, children, testId }: { title: React.ReactNode; aside?: React.ReactNode; children: React.ReactNode; testId?: string }) {
  return (
    <section className="detail-card" data-testid={testId}>
      <Group justify="space-between" align="baseline" mb={18} wrap="nowrap">
        <Title order={4} style={{ letterSpacing: "-0.02em" }}>{title}</Title>
        {aside}
      </Group>
      {children}
    </section>
  );
}

/** Пары «подпись — значение» в две колонки. */
export function KeyValues({ rows }: { rows: Array<[string, React.ReactNode]> }) {
  return (
    <Stack gap={4}>
      {rows.map(([k, v]) => (
        <Group key={k} gap="sm" wrap="nowrap" align="baseline">
          <Text size="xs" c="dimmed" style={{ flex: "0 0 160px" }}>{k}</Text>
          <Text size="sm" style={{ minWidth: 0 }}>{v}</Text>
        </Group>
      ))}
    </Stack>
  );
}

/** Кнопки-фильтры со счётчиками: роль композиции, класс слайда, вид ресурса. */
export function FilterChips({ counts, labels, active, onChange, total, testId }: { counts: Record<string, number>; labels: Record<string, string>; active: string | null; onChange: (key: string | null) => void; total: number; testId?: string }) {
  const keys = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
  return (
    <Group gap={6} data-testid={testId}>
      <Button variant={active === null ? "light" : "subtle"} color={active === null ? "brand" : "gray"} size="xs" aria-pressed={active === null} onClick={() => onChange(null)}>
        Все · {total}
      </Button>
      {keys.map((k) => (
        <Button key={k} variant={active === k ? "light" : "subtle"} color={active === k ? "brand" : "gray"} size="xs" aria-pressed={active === k} onClick={() => onChange(active === k ? null : k)}>
          {labels[k] ?? k} · {counts[k]}
        </Button>
      ))}
    </Group>
  );
}

/** Увеличенный слайд файла в окне. */
export function SlideLightbox({ src, title, caption, onClose }: { src?: string; title: string; caption?: React.ReactNode; onClose: () => void }) {
  return (
    <Modal opened={Boolean(src)} onClose={onClose} title={title} size="xl" centered>
      <SlideImage src={src} alt={title} />
      {caption && <Text size="xs" c="dimmed" mt="sm">{caption}</Text>}
    </Modal>
  );
}

export function countBy<T>(items: T[], key: (item: T) => string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const item of items) {
    const k = key(item);
    out[k] = (out[k] ?? 0) + 1;
  }
  return out;
}

export const percent = (v?: number | null) => (v == null ? "—" : `${Math.round(v * 100)} %`);
