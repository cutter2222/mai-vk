"use client";

import { Alert, Button, Container, SimpleGrid, Skeleton, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { CatalogHeader } from "@/components/common/CatalogHeader";
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
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const load = async () => {
      try {
        const list = await api.templates.list();
        if (cancelled) return;
        setItems(list);
        setError("");
        if (list.some((t) => ANALYZING.has(t.status))) timer = setTimeout(load, 3000);
      } catch {
        if (!cancelled) {
          setError("Не удалось обновить библиотеку. Проверьте соединение и повторите.");
          timer = setTimeout(load, 5000);
        }
      } finally {
        if (!cancelled) setLoaded(true);
      }
    };
    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [reload]);

  const open = (id: string) => router.push(`/templates?id=${encodeURIComponent(id)}`);

  /** Загрузка PPTX прямо в библиотеку: раньше шаблон попадал сюда только через чат проекта. */
  const add = async (file: File) => {
    if (!/\.pptx$/i.test(file.name)) {
      notifications.show({ color: "red", title: "Нужен файл PPTX", message: "Сохраните шаблон в формате .pptx и повторите загрузку." });
      return;
    }
    setAdding(true);
    try {
      const created = await api.templates.uploadFile(file);
      setReload((value) => value + 1);
      open(created.template_id);
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

  const shown = items.filter((item) => item.name.toLocaleLowerCase("ru").includes(query.trim().toLocaleLowerCase("ru"))
    && (status === "all" || (status === "analyzing" ? ANALYZING.has(item.status) : item.status === status)));

  return (
    <div className="page-surface">
      <Container size="xl" py="xl">
        <CatalogHeader
          title="Шаблоны"
          searchLabel="Поиск шаблонов"
          searchPlaceholder="Найти шаблон"
          query={query}
          onQueryChange={setQuery}
          filterLabel="Статус шаблонов"
          filter={status}
          onFilterChange={setStatus}
          options={[{ value: "all", label: "Все шаблоны" }, { value: "succeeded", label: "Готовы к работе" }, { value: "analyzing", label: "Анализируются" }, { value: "failed", label: "С ошибкой" }]}
        />
        {error && <Alert color="red" mb="lg" title="Библиотека недоступна">{error}<Button ml="sm" size="xs" variant="light" onClick={() => setReload((value) => value + 1)}>Повторить</Button></Alert>}

        {loaded && !error && items.length === 0 ? (
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
            <Text size="xs" c="dimmed">PPTX · автоматический анализ</Text>
          </button>
          {!loaded && [0, 1, 2].map((key) => <Skeleton key={key} height={230} radius="lg" />)}
          {shown.map((t) => (
            <TemplateCard key={t.template_id} item={t} onOpen={() => open(t.template_id)} onDelete={() => setDeleting(t)} />
          ))}
        </SimpleGrid>
        {loaded && items.length > 0 && shown.length === 0 && <Text ta="center" c="dimmed" py={60}>По вашему запросу шаблонов нет. Измените название или фильтр.</Text>}
      </Container>

      <DeleteTemplateModal target={deleting} onClose={() => setDeleting(null)} onDeleted={(id) => setItems((list) => list.filter((t) => t.template_id !== id))} />
    </div>
  );
}
