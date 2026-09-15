"use client";

import { Badge, Group, SegmentedControl, SimpleGrid, Stack, Text } from "@mantine/core";

import { SlideImage } from "@/components/common/SlideImage";
import { api } from "@/lib/api/client";
import type { GenerationResult } from "@/lib/api/types";

type Variant = GenerationResult["variants"][number];

interface Props {
  jobId: string;
  variant: Variant;
  revision: number;
  onRevision: (r: number) => void;
  issuesBefore?: number;
  issuesAfter?: number;
}

export function RevisionsPanel({ jobId, variant, revision, onRevision, issuesBefore, issuesAfter }: Props) {
  const revisions = variant.revisions ?? [];
  if (revisions.length <= 1) return null;
  const current = revisions.find((r) => r.revision === revision);
  const prevRev = revisions.find((r) => r.revision === revision - 1);
  const changed = current?.changed_slide_ids ?? [];
  // Идентификатор изменённого слайда в заглушке имеет вид s<номер>; в реальном результате это slide_id плана.
  const slideIndexFromId = (id: string) => {
    const m = id.match(/\d+/);
    return m ? Math.max(0, Number(m[0]) - 1) : 0;
  };

  return (
    <div data-testid="revisions-panel">
      <Group justify="space-between" mb="sm">
        <Group gap="xs">
          <Text fw={600}>Ревизии</Text>
          {revision !== variant.revision && <Badge color="yellow" size="xs">устаревшая ревизия</Badge>}
        </Group>
        <SegmentedControl size="xs" value={String(revision)} onChange={(v) => onRevision(Number(v))} data={revisions.map((r) => ({ value: String(r.revision), label: `r${r.revision}` }))} />
      </Group>
      {current && current.revision > 1 && (
        <Stack gap="sm">
          <Group gap="lg">
            <Text size="sm">Изменено слайдов: {changed.length}</Text>
            {issuesBefore != null && issuesAfter != null && (
              <Text size="sm" c="dimmed">находок: {issuesBefore} → {issuesAfter}</Text>
            )}
          </Group>
          <SimpleGrid cols={1} spacing="sm">
            {changed.map((sid) => {
              const idx = slideIndexFromId(sid);
              const name = (r: number) => `${variant.variant_id}/r${r}/thumbs/slide-${String(idx + 1).padStart(2, "0")}.png`;
              return (
                <div key={sid}>
                  <Text size="xs" c="dimmed" mb={4}>Слайд {idx + 1}</Text>
                  <Group grow gap="xs" align="flex-start">
                    <div>
                      <SlideImage src={api.generations.artifactUrl(jobId, name(prevRev?.revision ?? 1))} alt="до" />
                      <Text size="xs" ta="center" c="dimmed">до</Text>
                    </div>
                    <div>
                      <SlideImage src={api.generations.artifactUrl(jobId, name(current.revision))} alt="после" />
                      <Text size="xs" ta="center" c="dimmed">после</Text>
                    </div>
                  </Group>
                </div>
              );
            })}
          </SimpleGrid>
        </Stack>
      )}
    </div>
  );
}
