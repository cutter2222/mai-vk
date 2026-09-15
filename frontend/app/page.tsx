"use client";

import { Anchor, Divider, Group, Stack, Text } from "@mantine/core";
import Link from "next/link";
import { useState } from "react";

import { ContentStep } from "@/components/new/ContentStep";
import { SettingsStep } from "@/components/new/SettingsStep";
import { TemplateStep } from "@/components/new/TemplateStep";
import { loadLastJob } from "@/lib/state/draft";

export default function NewPresentationPage() {
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [packageId, setPackageId] = useState<string | null>(null);
  const [lastJob] = useState<string | null>(() => (typeof window === "undefined" ? null : loadLastJob()));

  return (
    <Stack gap="xl" py="md">
      {lastJob && (
        <Group gap="xs">
          <Text size="sm" c="dimmed">Есть незавершённая работа:</Text>
          <Anchor component={Link} href={`/workspace?job=${encodeURIComponent(lastJob)}`} size="sm" data-testid="continue-last">
            продолжить последнее задание
          </Anchor>
        </Group>
      )}
      <TemplateStep selectedId={templateId} onSelect={setTemplateId} />
      <Divider />
      <ContentStep packageId={packageId} onPackage={setPackageId} />
      <Divider />
      <SettingsStep templateId={templateId} packageId={packageId} />
    </Stack>
  );
}
