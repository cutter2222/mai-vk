"use client";

import { Button, Group, Modal, Table, Text, Title } from "@mantine/core";

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

/** Пары «подпись — значение»: та же таблица, что и остальные на странице шаблона, без шапки. */
export function KeyValues({ rows }: { rows: Array<[string, React.ReactNode]> }) {
  return (
    <Table fz="sm" verticalSpacing={6} className="detail-table detail-kv">
      <Table.Tbody>
        {rows.map(([k, v]) => (
          <Table.Tr key={k}>
            <Table.Th scope="row">{k}</Table.Th>
            <Table.Td>{v}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

/** Плитка величин: крупное число и подпись под ним — для полей слайда и статистики файла. */
export function StatGrid({ items, columns }: { items: Array<[string, React.ReactNode]>; columns?: number }) {
  return (
    <div className="stat-grid" style={columns ? { gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` } : undefined}>
      {items.map(([label, value]) => (
        <div key={label}><Text size="lg" fw={600} lh={1.1}>{value}</Text><Text size="xs" c="dimmed">{label}</Text></div>
      ))}
    </div>
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
