"use client";

import { Alert, Anchor, Button, Group, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCopy, IconExternalLink } from "@tabler/icons-react";
import { useMemo } from "react";

import { api } from "@/lib/api/client";
import type { TemplateProfile } from "@/lib/api/types";
import { formatBytes, formatDate } from "@/lib/format";

import { KeyValues, Section } from "./common";

async function copy(text: string, what: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    notifications.show({ color: "gray", message: `${what} скопирован в буфер обмена` });
  } catch {
    notifications.show({ color: "red", message: "Буфер обмена недоступен" });
  }
}

/** Что уходит в модель: дайджест профиля, предупреждения анализа и версии. */
export function DigestSection({ profile }: { profile: TemplateProfile }) {
  return (
    <Stack gap={28}>
      <Section title="Анализ" testId="digest-meta">
        <KeyValues
          rows={[
            ["Анализатор", `${profile.analyzer.name} ${profile.analyzer.version}`],
            ["Схема профиля", profile.schema_version],
            ["Файл", `${profile.source_file.name} · ${formatBytes(profile.source_file.size_bytes)}`],
            ["Хэш файла", <Text key="hash" size="xs" ff="monospace" style={{ wordBreak: "break-all" }}>{profile.template_hash}</Text>],
            ["Профиль создан", profile.created_at ? formatDate(profile.created_at) : "—"],
          ]}
        />
      </Section>
      <Section title="Предупреждения анализа" aside={<Text size="xs" c="dimmed">{profile.warnings?.length ?? 0}</Text>} testId="digest-warnings">
        {profile.warnings?.length ? (
          <Stack gap={6}>
            {profile.warnings.map((w, i) => (
              <Alert key={i} color="yellow" icon={<IconAlertTriangle size={16} />} title={w.code} py={6}>
                <Text size="sm">{w.message}{w.slide_index != null ? ` (слайд ${w.slide_index})` : ""}</Text>
              </Alert>
            ))}
          </Stack>
        ) : (
          <Text size="sm" c="dimmed">Анализ прошёл без предупреждений.</Text>
        )}
      </Section>
      <Section
        title="Дайджест для модели"
        aside={profile.llm_digest ? <Button variant="subtle" color="gray" size="compact-xs" leftSection={<IconCopy size={12} />} onClick={() => void copy(profile.llm_digest as string, "Дайджест")}>Скопировать</Button> : undefined}
        testId="digest-text"
      >
        {profile.llm_digest ? (
          <>
            <Text size="xs" c="dimmed" mb="xs">Компактное описание профиля, которое планировщики получают в промпте вместо полного JSON: {profile.llm_digest.length} символов.</Text>
            <pre className="profile-pre">{profile.llm_digest}</pre>
          </>
        ) : (
          <Text size="sm" c="dimmed">Дайджест не собран.</Text>
        )}
      </Section>
    </Stack>
  );
}

/** Профиль целиком: то, что хранит сервер и отдаёт в /api/templates/{id}. */
export function JsonSection({ templateId, profile }: { templateId: string; profile: TemplateProfile }) {
  const text = useMemo(() => JSON.stringify(profile, null, 2), [profile]);
  return (
    <Section
      title="JSON профиля"
      aside={
        <Group gap="xs">
          <Text size="xs" c="dimmed">{formatBytes(new Blob([text]).size)}</Text>
          <Button variant="subtle" color="gray" size="compact-xs" leftSection={<IconCopy size={12} />} onClick={() => void copy(text, "JSON")}>Скопировать</Button>
          <Anchor href={api.templates.detailUrl(templateId)} target="_blank" rel="noreferrer" size="xs" c="dimmed"><Group gap={4} wrap="nowrap"><IconExternalLink size={12} />Открыть ответ API</Group></Anchor>
        </Group>
      }
      testId="profile-json"
    >
      <pre className="profile-pre" style={{ maxHeight: "70vh" }}>{text}</pre>
    </Section>
  );
}
