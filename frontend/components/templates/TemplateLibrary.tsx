"use client";

import { Container, Group, SimpleGrid, Stack, Text, Title } from "@mantine/core";
import { IconTemplate } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, type TemplateListItem } from "@/lib/api/client";

import { DeleteTemplateModal } from "./DeleteTemplateModal";
import { TemplateCard } from "./TemplateCard";

const ANALYZING = new Set(["queued", "running"]);

/** Сетка библиотеки шаблонов. Пока какой-то шаблон анализируется, список перечитывается. */
export function TemplateLibrary() {
  const router = useRouter();
  const [items, setItems] = useState<TemplateListItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [deleting, setDeleting] = useState<TemplateListItem | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const load = async () => {
      try {
        const list = await api.templates.list();
        if (cancelled) return;
        setItems(list);
        if (list.some((t) => ANALYZING.has(t.status))) timer = setTimeout(load, 3000);
      } catch {
        if (!cancelled) setItems([]);
      } finally {
        if (!cancelled) setLoaded(true);
      }
    };
    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  const open = (id: string) => router.push(`/templates?id=${encodeURIComponent(id)}`);

  return (
    <div className="page-surface">
      <Container size="xl" py="xl">
        <Group justify="space-between" align="flex-end" mb="lg">
          <div>
            <Title order={2} style={{ letterSpacing: "-0.02em" }}>Шаблоны</Title>
            <Text c="dimmed" size="sm" mt={4}>Библиотека оформлений: каждый PPTX разобран на композиции, палитру, шрифты и правила. Откройте шаблон, чтобы увидеть, что из него извлечено.</Text>
          </div>
          {items.length > 0 && <Text size="sm" c="dimmed">{items.length} в библиотеке</Text>}
        </Group>

        {loaded && items.length === 0 ? (
          <Stack align="center" gap="sm" py={80} data-testid="templates-empty">
            <IconTemplate size={48} stroke={1.2} color="var(--mantine-color-gray-5)" />
            <Title order={4}>Библиотека пуста</Title>
            <Text c="dimmed" size="sm" ta="center" maw={440}>Шаблон появляется здесь после загрузки PPTX в чат проекта: сервис разбирает образцы, палитру и шрифты и сохраняет профиль для всех презентаций.</Text>
          </Stack>
        ) : (
          <SimpleGrid cols={{ base: 1, sm: 2, md: 3, lg: 4 }} spacing="lg" data-testid="templates-grid">
            {items.map((t) => (
              <TemplateCard key={t.template_id} item={t} onOpen={() => open(t.template_id)} onDelete={() => setDeleting(t)} />
            ))}
          </SimpleGrid>
        )}
      </Container>

      <DeleteTemplateModal target={deleting} onClose={() => setDeleting(null)} onDeleted={(id) => setItems((list) => list.filter((t) => t.template_id !== id))} />
    </div>
  );
}
