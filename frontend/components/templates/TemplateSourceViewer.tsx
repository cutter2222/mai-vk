"use client";

import { Alert, Button, Loader, Text } from "@mantine/core";
import { useEffect, useId, useState } from "react";

import { loadSDK } from "@/components/office/OfficeEditor";
import { api } from "@/lib/api/client";

/** Только исходный файл: без редактирования, callback сохранения и офисных копий. */
export function TemplateSourceViewer({ templateId }: { templateId: string }) {
  const hostId = useId();
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let editor: { destroyEditor: () => void } | undefined;
    const timeout = setTimeout(() => {
      if (!cancelled) setError("Просмотр не загрузился за 60 секунд. Можно повторить или скачать исходный PPTX.");
    }, 60_000);
    void (async () => {
      try {
        const result = await api.office.templateConfig(templateId);
        await loadSDK(result.script_url);
        if (cancelled) return;
        if (!window.DocsAPI) throw new Error("ONLYOFFICE не предоставил API просмотра.");
        editor = new window.DocsAPI.DocEditor(hostId, {
          ...result.config,
          events: {
            onDocumentReady: () => { if (!cancelled) { clearTimeout(timeout); setError(""); setReady(true); } },
            onError: () => { if (!cancelled) { clearTimeout(timeout); setError("ONLYOFFICE не смог открыть файл. Скачайте исходник или повторите загрузку."); } },
          },
        });
      } catch (e) {
        if (!cancelled) { clearTimeout(timeout); setError(e instanceof Error ? e.message : "Не удалось открыть файл"); }
      }
    })();
    return () => { cancelled = true; clearTimeout(timeout); editor?.destroyEditor(); };
  }, [templateId, hostId, attempt]);

  return (
    <div className="tpl-source-viewer" data-testid="template-source-viewer">
      {error && <Alert color="red" title="Просмотр недоступен">
        {error}
        <Button ml="sm" size="xs" variant="light" onClick={() => { setReady(false); setError(""); setAttempt((n) => n + 1); }}>Повторить</Button>
      </Alert>}
      <div className="office-canvas" aria-label="Просмотр исходного шаблона ONLYOFFICE">
        {!ready && !error && <div className="office-loading"><Loader size="sm" /><Text size="sm">Открываем исходный PPTX…</Text></div>}
        <div id={hostId} />
      </div>
    </div>
  );
}