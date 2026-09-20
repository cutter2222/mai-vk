"use client";

import { Container, Group, SimpleGrid, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { api, ApiError, type TemplateListItem } from "@/lib/api/client";

import { DeleteTemplateModal } from "./DeleteTemplateModal";
import { TemplateCard } from "./TemplateCard";

const ANALYZING = new Set(["queued", "running"]);

/** Сетка библиотеки шаблонов. Пока какой-то шаблон анализируется, список перечитывается. */
export function TemplateLibrary() {
  const router = useRouter();
  const [items, setItems] = useState<TemplateListItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [deleting, setDeleting] = useState<TemplateListItem | null>(null);
  const [adding, setAdding] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

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

  /** Загрузка PPTX прямо в библиотеку: раньше шаблон попадал сюда только через чат проекта. */
  const add = async (file: File) => {
    setAdding(true);
    try {
      const created = await api.templates.uploadFile(file);
      const list = await api.templates.list();
      setItems(list);
      if (created.cached) open(created.template_id);
    } catch (e) {
      notifications.show({
        color: "red",
        title: "Шаблон не загружен",
        message: e instanceof ApiError ? e.message : "Сервер недоступен",
      });
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="page-surface">
      <Container size="xl" py="xl">
        {/* Добавление — первой карточкой сетки, как на экране презентаций. */}
        <Group justify="space-between" align="center" mb={28}>
          <Title order={1} style={{ letterSpacing: "-0.03em" }}>Шаблоны</Title>
          {items.length > 0 && <Text size="sm" c="dimmed">{items.length} в библиотеке</Text>}
        </Group>

        {loaded && items.length === 0 ? (
          <Text c="dimmed" size="sm" mb="lg" data-testid="templates-empty" maw={560}>
            Пока пусто. Добавьте PPTX — сервис разберёт его на композиции, палитру, шрифты и
            правила, и шаблон станет доступен всем презентациям.
          </Text>
        ) : null}

        <input
          ref={fileInput}
          type="file"
          accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation"
          hidden
          onChange={(e) => {
            const file = e.currentTarget.files?.[0];
            e.currentTarget.value = "";
            if (file) void add(file);
          }}
          data-testid="template-file-input"
        />
        <SimpleGrid cols={{ base: 1, sm: 2, md: 3, lg: 4 }} spacing="lg" data-testid="templates-grid">
          <button
            type="button"
            className="grid-card-new"
            onClick={() => fileInput.current?.click()}
            disabled={adding}
            data-testid="new-template"
          >
            <IconPlus size={26} stroke={1.6} />
            <Text size="sm" fw={600}>{adding ? "Загружаю…" : "Добавить шаблон"}</Text>
          </button>
          {items.map((t) => (
            <TemplateCard key={t.template_id} item={t} onOpen={() => open(t.template_id)} onDelete={() => setDeleting(t)} />
          ))}
        </SimpleGrid>
      </Container>

      <DeleteTemplateModal target={deleting} onClose={() => setDeleting(null)} onDeleted={(id) => setItems((list) => list.filter((t) => t.template_id !== id))} />
    </div>
  );
}
