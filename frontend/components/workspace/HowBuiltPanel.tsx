"use client";

import { Badge, Card, Code, Group, Stack, Text } from "@mantine/core";
import { useEffect, useState } from "react";

import { SlideImage } from "@/components/common/SlideImage";
import { api } from "@/lib/api/client";
import type { ContentPackage, GenerationResult, SlidePlan, TemplateProfile } from "@/lib/api/types";
import { PATTERN_ROLE_LABELS } from "@/lib/format";

interface Props {
  jobId: string;
  result: GenerationResult;
  variantId: string;
  slideIndex: number;
}

/** Прозрачность результата: паттерн шаблона, факты с исходными фрагментами, применённые исправления. */
export function HowBuiltPanel({ jobId, result, variantId, slideIndex }: Props) {
  const [plan, setPlan] = useState<SlidePlan | null>(null);
  const [profile, setProfile] = useState<TemplateProfile | null>(null);
  const [pkg, setPkg] = useState<ContentPackage | null>(null);
  const variant = result.variants.find((v) => v.variant_id === variantId);

  useEffect(() => {
    let alive = true;
    const planName = variant?.plan_artifact;
    if (planName) {
      fetch(api.generations.artifactUrl(jobId, planName)).then((r) => r.json()).then((p) => alive && setPlan(p as SlidePlan)).catch(() => undefined);
    }
    api.templates.get(result.template_id).then((t) => alive && t.profile && setProfile(t.profile)).catch(() => undefined);
    api.content.get(result.package_id).then((c) => alive && c.package && setPkg(c.package)).catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [jobId, result.template_id, result.package_id, variant?.plan_artifact]);

  type PlanSlide = SlidePlan["slides"][number];
  const slide: PlanSlide | undefined = plan?.slides.find((s) => s.order === slideIndex + 1) ?? plan?.slides[slideIndex];
  const pattern = profile?.patterns.find((p) => p.pattern_id === slide?.pattern_id);
  const factIds = new Set<string>([...(slide?.fact_refs ?? []), ...(slide?.blocks.flatMap((b: PlanSlide["blocks"][number]) => b.fact_refs ?? []) ?? [])]);
  const facts = pkg?.facts.filter((f) => factIds.has(f.fact_id)) ?? [];
  const repairs = result.repairs?.filter((r) => r.variant_id === variantId && r.changed_slide_ids?.includes(slide?.slide_id ?? "")) ?? [];
  const edits = result.edits?.filter((e) => e.variant_id === variantId && e.result === "applied" && e.slide_id === slide?.slide_id) ?? [];

  if (!slide) {
    return (
      <Card>
        <Text size="sm" c="dimmed">Для этого слайда план ещё не доступен.</Text>
      </Card>
    );
  }

  return (
    <Card data-testid="how-built">
      <Text fw={600} mb="xs">Как собран слайд {slideIndex + 1}</Text>
      <Group align="flex-start" gap="lg" wrap="nowrap">
        <div style={{ width: 220, flex: "0 0 220px" }}>
          <Text size="xs" c="dimmed" mb={4}>Паттерн шаблона</Text>
          <SlideImage src={pattern?.preview_path && profile ? api.templates.assetUrl(profile.template_id, pattern.preview_path) : undefined} alt={pattern?.name ?? ""} />
          <Text size="xs" mt={4}>{pattern ? `${PATTERN_ROLE_LABELS[pattern.role] ?? pattern.role} · ${pattern.name}` : slide.pattern_id}</Text>
          {pattern?.source.slide_index != null && <Text size="xs" c="dimmed">из образца №{pattern.source.slide_index + 1}, уверенность {Math.round((pattern.confidence ?? 0) * 100)} %</Text>}
        </div>
        <Stack gap="xs" style={{ flex: 1 }}>
          <div>
            <Text size="xs" c="dimmed">Ключевая мысль</Text>
            <Text size="sm">{slide.key_message ?? slide.title}</Text>
          </div>
          <div>
            <Text size="xs" c="dimmed" mb={4}>Факты из источников {facts.length ? "" : "— на слайде нет фактов"}</Text>
            <Stack gap={4}>
              {facts.map((f) => (
                <Group key={f.fact_id} gap="xs" wrap="nowrap" align="flex-start">
                  <Code>{f.raw}</Code>
                  <Text size="xs" c="dimmed">{f.label}{f.source_location?.fragment ? ` · «${f.source_location.fragment}»` : f.source_location?.cell ? ` · ячейка ${f.source_location.cell}` : ""}</Text>
                </Group>
              ))}
            </Stack>
          </div>
          <div>
            <Text size="xs" c="dimmed" mb={4}>Слоты</Text>
            <Group gap={4}>
              {slide.blocks.map((b: PlanSlide["blocks"][number]) => <Badge key={b.slot_id} size="xs" variant="outline" color="gray">{b.slot_id}: {b.kind}</Badge>)}
            </Group>
          </div>
          {repairs.length > 0 && (
            <div>
              <Text size="xs" c="dimmed" mb={4}>Применённые исправления</Text>
              {repairs.map((r) => <Text key={r.repair_job_id} size="xs">ревизия {r.new_revision}: исправлено находок {r.issue_ids.length}</Text>)}
            </div>
          )}
          {edits.length > 0 && (
            <div>
              <Text size="xs" c="dimmed" mb={4}>Правки по запросу</Text>
              {edits.map((e) => <Text key={e.edit_job_id} size="xs" data-testid="how-built-edit">ревизия {e.new_revision}: «{e.instruction}» — {e.change_note ?? "применено"}</Text>)}
            </div>
          )}
          {slide.revision_note && <Text size="xs" c="dimmed">Последняя правка: {slide.revision_note}</Text>}
          {slide.thesis_refs && slide.thesis_refs.length > 0 && <Text size="xs" c="dimmed">Тезисы плана: {slide.thesis_refs.join(", ")}</Text>}
        </Stack>
      </Group>
    </Card>
  );
}
