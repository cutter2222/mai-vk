"use client";

import { Chip, Group, Loader, Text, TextInput } from "@mantine/core";
import { IconSearch } from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";

import { api, type TemplateDetail } from "@/lib/api/client";
import type { Asset } from "@/lib/api/types";
import { usePolling } from "@/lib/api/usePolling";
import { imageDragProps } from "@/lib/state/drag";

const KIND_LABELS: Record<string, string> = { icon: "Иконки", photo: "Фото", logo: "Логотипы", image: "Картинки", background: "Фоны", screenshot: "Скриншоты", mockup: "Мокапы", chart_image: "Графики", qr: "QR" };

/** Картинки, которые покажет браузер и примет редактор слайдов; EMF/WMF и векторные — нет. */
const RASTER = /\.(png|jpe?g|gif|bmp)$/i;

/** Ресурсы шаблона для сетки: только переиспользуемые картинки пакета, без повторов байтов. */
export function templateAssets(detail: TemplateDetail | null): Asset[] {
  const seen = new Set<string>();
  return (detail?.profile?.assets ?? []).filter((a) => {
    if (a.reusable === false || !a.media_path.startsWith("ppt/media/") || !RASTER.test(a.media_path) || seen.has(a.sha256)) return false;
    seen.add(a.sha256);
    return true;
  });
}

/**
 * Вкладка «Из шаблона»: иконки, логотипы и картинки из профиля шаблона проекта по видам,
 * с поиском по тегам анализа. В сетке миниатюры сервера, по щелчку — оригинал.
 */
export function TemplateAssets({ templateId, hidden, onCount }: { templateId: string | null; hidden: boolean; onCount: (count: number | null) => void }) {
  const detail = usePolling<TemplateDetail>(templateId ? () => api.templates.get(templateId) : null, (d) => d.status === "succeeded" || d.status === "failed", [templateId]);
  const current = templateId ? detail.data : null;
  const assets = useMemo(() => templateAssets(current), [current]);
  const kinds = useMemo(() => [...new Set(assets.map((a) => a.kind))].sort((a, b) => Object.keys(KIND_LABELS).indexOf(a) - Object.keys(KIND_LABELS).indexOf(b)), [assets]);
  const [kind, setKind] = useState("all");
  const [query, setQuery] = useState("");
  const status = current?.status;
  useEffect(() => { onCount(status === "succeeded" ? assets.length : null); }, [status, assets.length, onCount]);

  if (hidden) return null;
  if (!templateId) return <Text size="sm" c="dimmed" py="md" data-testid="template-assets-empty">Шаблон не выбран. Выберите его вверху справа — здесь появятся его картинки, иконки и логотипы.</Text>;
  if (!current || status === "queued" || status === "running") return <Group gap="xs" py="md"><Loader size="xs" /><Text size="sm" c="dimmed">Шаблон разбирается…</Text></Group>;
  if (status === "failed") return <Text size="sm" c="dimmed" py="md">Разбор шаблона не удался: ресурсов нет.</Text>;
  if (!assets.length) return <Text size="sm" c="dimmed" py="md" data-testid="template-assets-empty">В шаблоне нет картинок, которые можно переиспользовать.</Text>;

  const needle = query.trim().toLowerCase();
  const shown = assets.filter((a) => (kind === "all" || a.kind === kind) && (!needle || (a.tags ?? []).some((t) => t.toLowerCase().includes(needle))));
  return (
    <div className="template-assets" data-testid="template-assets">
      <div className="template-assets-filters">
        {kinds.length > 1 && (
          <Chip.Group value={kind} onChange={(v) => setKind(v as string)}>
            <Group gap={6} wrap="wrap">
              <Chip value="all" size="xs" variant="light">Все</Chip>
              {kinds.map((k) => <Chip key={k} value={k} size="xs" variant="light" wrapperProps={{ "data-testid": `asset-kind-${k}` }}>{KIND_LABELS[k] ?? k}</Chip>)}
            </Group>
          </Chip.Group>
        )}
        <TextInput size="xs" leftSection={<IconSearch size={14} />} placeholder="По тегам: рост, деньги, команда…" value={query} onChange={(e) => setQuery(e.currentTarget.value)} data-testid="asset-search" />
      </div>
      {shown.length === 0 ? (
        <Text size="sm" c="dimmed" py="sm">Ничего не нашлось.</Text>
      ) : (
        <div className="file-grid file-grid-assets">
          {shown.map((a) => {
            const url = api.templates.mediaUrl(templateId, a.asset_id);
            const caption = a.tags?.slice(0, 2).join(", ") || KIND_LABELS[a.kind] || a.kind;
            return (
              <div key={a.asset_id} className="file-card asset-card" data-testid={`template-asset-${a.asset_id}`} title={`${[KIND_LABELS[a.kind] ?? a.kind, ...(a.tags ?? [])].join(" · ")} — перетащите на слайд`}
                {...imageDragProps({ template_id: templateId, asset_id: a.asset_id, name: caption })}>
                <a className="file-thumb" href={url} target="_blank" rel="noreferrer" draggable={false} aria-label={`Открыть ${caption}`}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={api.templates.mediaThumbnailUrl(templateId, a.asset_id)} alt="" loading="lazy" draggable={false} />
                </a>
                <div className="file-meta">
                  <Text size="xs" truncate>{caption}</Text>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
