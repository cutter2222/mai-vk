"use client";

import { Badge, Card, ColorSwatch, Group, SimpleGrid, Stack, Table, Text, Title, Tooltip } from "@mantine/core";
import { useEffect, useState } from "react";

import { SlideImage } from "@/components/common/SlideImage";
import { StatusBadge } from "@/components/common/StatusBadge";
import { api, type TemplateDetail, type TemplateListItem } from "@/lib/api/client";
import { PATTERN_ROLE_LABELS } from "@/lib/format";

export default function TemplatesPage() {
  const [items, setItems] = useState<TemplateListItem[]>([]);
  const [details, setDetails] = useState<Record<string, TemplateDetail>>({});

  useEffect(() => {
    api.templates.list().then(async (list) => {
      setItems(list);
      const entries = await Promise.all(list.map((t) => api.templates.get(t.template_id).then((d) => [t.template_id, d] as const).catch(() => null)));
      setDetails(Object.fromEntries(entries.filter((e): e is readonly [string, TemplateDetail] => e !== null)));
    }).catch(() => setItems([]));
  }, []);

  return (
    <Stack gap="lg" py="md">
      <div>
        <Title order={3}>Шаблоны и их дизайн-системы</Title>
        <Text c="dimmed" size="sm">Что сервис извлёк из каждого шаблона: палитра, шрифты, шкала кеглей, композиции из образцов, фиксированные элементы.</Text>
      </div>
      {items.length === 0 && <Text c="dimmed">Шаблонов пока нет. Загрузите первый на главной.</Text>}
      {items.map((t) => {
        const p = details[t.template_id]?.profile;
        return (
          <Card key={t.template_id} data-testid={`template-profile-${t.template_id}`}>
            <Group justify="space-between" mb="sm">
              <Group gap="sm">
                <Text fw={600}>{t.name}</Text>
                <StatusBadge status={t.status === "succeeded" ? "ready" : t.status} />
              </Group>
              {p && (
                <Group gap="xs">
                  <Badge variant="light">{p.patterns.length} паттернов</Badge>
                  <Badge variant="light" color="gray">{p.stats.slides} слайдов</Badge>
                  <Badge variant="light" color="gray">{p.slide_size.width_emu} × {p.slide_size.height_emu} EMU</Badge>
                  <Badge variant="light" color="gray">анализатор {p.analyzer.version}</Badge>
                </Group>
              )}
            </Group>
            {!p ? (
              <Text size="sm" c="dimmed">Профиль ещё не готов.</Text>
            ) : (
              <Stack gap="md">
                <SimpleGrid cols={{ base: 1, md: 4 }} spacing="lg">
                  <div>
                    <Text size="xs" c="dimmed" mb={6}>Палитра</Text>
                    <Group gap={6}>
                      {p.design_tokens.colors.palette.map((c) => (
                        <Tooltip key={c.hex} label={`${c.hex} · ${c.role} · ${c.usage_count ?? 0} использований`} withArrow>
                          <ColorSwatch color={c.hex} size={26} />
                        </Tooltip>
                      ))}
                    </Group>
                    <Text size="xs" c="dimmed" mt={6}>Тема: {Object.entries(p.design_tokens.colors.theme).slice(0, 6).map(([k, v]) => `${k} ${v}`).join(", ")}</Text>
                  </div>
                  <div>
                    <Text size="xs" c="dimmed" mb={6}>Шрифты</Text>
                    <Table fz="xs" verticalSpacing={2} withRowBorders={false}>
                      <Table.Tbody>
                        {p.design_tokens.typography.fonts.map((f) => (
                          <Table.Tr key={f.family}><Table.Td>{f.family}</Table.Td><Table.Td c="dimmed">{f.roles?.join(", ")}</Table.Td><Table.Td c="dimmed">{f.embedded ? "встроен" : f.fallback ? `замена: ${f.fallback}` : ""}</Table.Td></Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                  </div>
                  <div>
                    <Text size="xs" c="dimmed" mb={6}>Шкала кеглей</Text>
                    <Group gap={6}>
                      {p.design_tokens.typography.scale.map((s) => <Badge key={`${s.size_pt}${s.role}`} variant="outline" color="gray">{s.size_pt} pt · {s.role}</Badge>)}
                    </Group>
                  </div>
                  <div>
                    <Text size="xs" c="dimmed" mb={6}>Фиксированные элементы и правила</Text>
                    <Stack gap={2}>
                      {p.fixed_elements.map((f) => <Text key={f.element_id} size="xs">{f.kind} · {f.appears_on}</Text>)}
                      {p.guidelines?.slice(0, 3).map((g, i) => <Text key={i} size="xs" c="dimmed">«{g.text}»</Text>)}
                    </Stack>
                  </div>
                </SimpleGrid>
                <div>
                  <Text size="xs" c="dimmed" mb={6}>Композиции из образцов</Text>
                  <SimpleGrid cols={{ base: 2, md: 5 }} spacing="sm">
                    {p.patterns.map((pat) => (
                      <div key={pat.pattern_id}>
                        <SlideImage src={pat.preview_path ? api.templates.assetUrl(p.template_id, pat.preview_path) : undefined} alt={pat.name ?? pat.role} />
                        <Group gap={4} mt={4} wrap="nowrap">
                          <Badge size="xs" variant="light">{PATTERN_ROLE_LABELS[pat.role] ?? pat.role}</Badge>
                          <Text size="xs" truncate>{pat.name}</Text>
                        </Group>
                        <Text size="xs" c="dimmed">{pat.slots.length} слотов · уверенность {Math.round((pat.confidence ?? 0) * 100)} %</Text>
                      </div>
                    ))}
                  </SimpleGrid>
                </div>
                {p.llm_digest && (
                  <div>
                    <Text size="xs" c="dimmed" mb={4}>Что уходит в модель (дайджест профиля)</Text>
                    <Text size="xs">{p.llm_digest}</Text>
                  </div>
                )}
              </Stack>
            )}
          </Card>
        );
      })}
    </Stack>
  );
}
