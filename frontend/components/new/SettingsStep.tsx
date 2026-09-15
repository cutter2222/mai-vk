"use client";

import { Alert, Button, Card, Checkbox, Collapse, Group, NumberInput, SegmentedControl, Stack, Switch, Text, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconRocket, IconX } from "@tabler/icons-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, ApiError, type CapabilitiesResponse } from "@/lib/api/client";
import type { GenerationRequest } from "@/lib/api/types";
import { VARIANT_LABELS } from "@/lib/format";
import { saveLastJob } from "@/lib/state/draft";

interface Props {
  templateId: string | null;
  packageId: string | null;
}

export function SettingsStep({ templateId, packageId }: Props) {
  const router = useRouter();
  const [caps, setCaps] = useState<CapabilitiesResponse | null>(null);
  const [mode, setMode] = useState<"range" | "exact">("range");
  const [min, setMin] = useState<number | string>(10);
  const [max, setMax] = useState<number | string>(15);
  const [exact, setExact] = useState<number | string>(12);
  const [variants, setVariants] = useState<string[]>(["compact", "balanced", "detailed"]);
  const [contextual, setContextual] = useState(true);
  const [images, setImages] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [seed, setSeed] = useState<number | string>("");
  const [force, setForce] = useState(false);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    api.capabilities().then(setCaps).catch(() => setCaps(null));
  }, []);

  const rangeError = mode === "range" && Number(min) > Number(max) ? "Минимум больше максимума" : null;
  const ready = Boolean(templateId && packageId) && variants.length > 0 && !rangeError;

  const start = async () => {
    if (!templateId || !packageId) return;
    setStarting(true);
    const req: GenerationRequest = {
      schema_version: "1.1",
      template_id: templateId,
      package_id: packageId,
      idempotency_key: `ui-${Date.now().toString(36)}`,
      settings: {
        slide_count: mode === "exact" ? { exact: Number(exact) } : { min: Number(min), max: Number(max) },
        language: "ru",
        variants: variants as GenerationRequest["settings"] extends { variants?: infer V } ? V : never,
        generate_images: images,
        run_contextual_audit: contextual,
        force_regenerate: force,
        ...(seed !== "" ? { seed: Number(seed) } : {}),
      },
    };
    try {
      const res = await api.generations.create(req);
      saveLastJob(res.job_id);
      router.push(`/workspace?job=${encodeURIComponent(res.job_id)}`);
    } catch (e) {
      notifications.show({ color: "red", title: "Генерация не запущена", message: e instanceof ApiError ? e.message : "Неизвестная ошибка", icon: <IconX size={16} /> });
      setStarting(false);
    }
  };

  return (
    <Stack gap="md">
      <div>
        <Title order={3}>3. Настройки и запуск</Title>
        <Text c="dimmed" size="sm">Три варианта вёрстки различаются плотностью подачи и одинаково соответствуют правилам шаблона.</Text>
      </div>
      <Card>
        <Stack gap="md">
          <div>
            <Text size="sm" fw={500} mb={6}>Варианты вёрстки</Text>
            <Checkbox.Group value={variants} onChange={setVariants}>
              <Group gap="lg">
                {(["compact", "balanced", "detailed"] as const).map((v) => (
                  <Checkbox key={v} value={v} label={VARIANT_LABELS[v]} data-testid={`variant-${v}`} />
                ))}
              </Group>
            </Checkbox.Group>
          </div>
          <div>
            <Text size="sm" fw={500} mb={6}>Число слайдов</Text>
            <Group align="flex-end" gap="md">
              <SegmentedControl value={mode} onChange={(v) => setMode(v as "range" | "exact")} data={[{ value: "range", label: "Диапазон" }, { value: "exact", label: "Точно" }]} />
              {mode === "range" ? (
                <>
                  <NumberInput label="от" min={1} max={caps?.limits.slide_count_max ?? 60} value={min} onChange={setMin} w={90} data-testid="slides-min" />
                  <NumberInput label="до" min={1} max={caps?.limits.slide_count_max ?? 60} value={max} onChange={setMax} w={90} data-testid="slides-max" error={rangeError} />
                </>
              ) : (
                <NumberInput label="ровно" min={1} max={caps?.limits.slide_count_max ?? 60} value={exact} onChange={setExact} w={100} />
              )}
              <Text size="xs" c="dimmed" pb={8}>По ТЗ целевой объём 10–15 слайдов; точное число соблюдается во всех вариантах.</Text>
            </Group>
          </div>
          <Group gap="xl">
            <Switch checked={contextual} onChange={(e) => setContextual(e.currentTarget.checked)} label="Контекстный аудит моделью" description="11 вопросов по каждому слайду. Без него аудит неполный, результат получит статус «требует проверки»." />
            <Tooltip label={caps?.features.generate_images ? "" : "Генерация новых изображений доступна только после этапа топ-10"} disabled={caps?.features.generate_images} withArrow>
              <div>
                <Switch checked={images} onChange={(e) => setImages(e.currentTarget.checked)} disabled={!caps?.features.generate_images} label="Генерировать новые изображения" description="Картинки из контент-пакета используются всегда" />
              </div>
            </Tooltip>
          </Group>
          <Button variant="subtle" size="compact-sm" onClick={() => setAdvanced((a) => !a)} w="fit-content">
            {advanced ? "Скрыть расширенные настройки" : "Расширенные настройки"}
          </Button>
          <Collapse expanded={advanced}>
            <Group gap="lg" align="flex-end" pt="xs">
              <NumberInput label="Seed" description="Передаётся провайдеру; побитовая воспроизводимость не гарантируется" value={seed} onChange={setSeed} w={220} />
              <Switch checked={force} onChange={(e) => setForce(e.currentTarget.checked)} label="Явная перегенерация" description="Не использовать кэш ответов модели" />
            </Group>
          </Collapse>
        </Stack>
      </Card>

      {!templateId || !packageId ? (
        <Alert color="gray">Для запуска нужны {!templateId ? "шаблон" : ""}{!templateId && !packageId ? " и " : ""}{!packageId ? "содержание" : ""}. Дожидаться окончания анализа шаблона не нужно.</Alert>
      ) : null}

      <Group>
        <Button size="md" leftSection={<IconRocket size={18} />} onClick={start} disabled={!ready} loading={starting} data-testid="generate">
          Сгенерировать
        </Button>
        <Text size="xs" c="dimmed">Задание дождётся анализа и импорта, затем построит смысловой план и три варианта параллельно.</Text>
      </Group>
    </Stack>
  );
}
