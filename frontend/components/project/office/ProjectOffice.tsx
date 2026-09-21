"use client";

import { Alert, Button, Loader, Menu, Select, Stack, Text } from "@mantine/core";
import { useEffect, useState, type Ref } from "react";

import { OfficeEditor, type OfficeEditHandle } from "@/components/office/OfficeEditor";
import { api, type OfficeDocument } from "@/lib/api/client";
import { VARIANT_LABELS } from "@/lib/format";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";

/** Pin the open artifact: a background AI revision must not destroy a live office session. */
export function ProjectOffice({ session, title, editRef, actionsTarget }: { session: GenerationSession; title: string; editRef?: Ref<OfficeEditHandle>; actionsTarget?: HTMLElement | null }) {
  const variant = session.variant;
  const latest = session.jobId && variant?.artifacts?.pptx
    ? { jobId: session.jobId, artifact: variant.artifacts.pptx, variantId: variant.variant_id, revision: variant.revision }
    : null;
  // Mounted only once a PPTX is available; later generation may temporarily have no artifact.
  const [source, setSource] = useState(() => latest!);
  const [doc, setDoc] = useState<OfficeDocument | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [active, setActive] = useState(true);
  const changed = latest && (source.jobId !== latest.jobId || source.artifact !== latest.artifact);

  useEffect(() => {
    let cancelled = false;
    // Idempotent on the server: revisiting the project reopens saved office edits.
    void api.office.create(source.jobId, source.artifact).then((value) => {
      if (!cancelled) setDoc(value);
    }).catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [source.jobId, source.artifact, attempt]);

  const openLatest = () => {
    if (!latest || active) return;
    setDoc(null);
    setError("");
    setActive(true);
    setSource(latest);
  };

  return (
    <div className="project-office" data-testid="project-office">
      {!session.terminal && <Text size="xs" c="dimmed" p="xs" role="status">Готовый вариант уже открыт. Остальные варианты и проверки выполняются в фоне; этот PPTX не заменяется.</Text>}
      {changed && <Alert color="blue" title="Доступна другая версия презентации">
        Открытый PPTX не заменён: ваши офисные правки остаются в нём.
        {active ? " Завершите редактирование, чтобы открыть другую версию." : <Button ml="sm" size="xs" onClick={openLatest}>Открыть выбранную версию</Button>}
      </Alert>}
      {error ? <Alert color="red" title="Редактор не открыт">
        {error}
        <Button ml="sm" size="xs" variant="light" onClick={() => { setError(""); setAttempt((n) => n + 1); }}>Повторить открытие</Button>
      </Alert> : doc ? (
        <OfficeEditor key={doc.id} id={doc.id} title={title} embedded editRef={editRef} actionsTarget={actionsTarget} onActiveChange={setActive} documentActions={<>
          <Menu.Divider />
          <Menu.Label>{VARIANT_LABELS[source.variantId] ?? source.variantId} · сборка r{source.revision}</Menu.Label>
          {(session.result?.variants.length ?? 0) > 1 && <Select mx="xs" mb="xs" size="xs" aria-label="Вариант презентации" value={session.selectedVariant}
            comboboxProps={{ withinPortal: false }} disabled={active}
            onChange={(value) => { if (value && !active) session.setSelectedVariant(value); }}
            data={(session.result?.variants ?? []).filter((v) => v.artifacts?.pptx).map((v) => ({ value: v.variant_id, label: VARIANT_LABELS[v.variant_id] ?? v.variant_id }))} />}
          <Text size="xs" c="dimmed" px="sm">Для смены варианта завершите редактирование. Ручные правки остаются в текущем PPTX и не переносятся в новую сборку.</Text>
        </>} />
      ) : <Stack align="center" justify="center" flex={1}><Loader size="sm" /><Text size="sm" c="dimmed">Открываем PPTX в ONLYOFFICE…</Text></Stack>}
    </div>
  );
}