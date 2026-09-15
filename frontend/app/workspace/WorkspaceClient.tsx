"use client";

import { Button, Stack, Text, Title } from "@mantine/core";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { WorkspaceView } from "@/components/workspace/WorkspaceView";

export function WorkspaceClient() {
  const params = useSearchParams();
  const jobId = params.get("job");
  if (!jobId) {
    return (
      <Stack py="xl" align="flex-start" data-testid="workspace-empty">
        <Title order={3}>Не указано задание</Title>
        <Text c="dimmed">Откройте рабочее пространство по ссылке вида /workspace?job=… или начните новую презентацию.</Text>
        <Button component={Link} href="/" variant="light">К новой презентации</Button>
      </Stack>
    );
  }
  return <WorkspaceView jobId={jobId} />;
}
