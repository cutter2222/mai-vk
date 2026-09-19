"use client";

import { Badge, Button, FileButton, Group, Loader, SegmentedControl, Stack, Tabs, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconUpload } from "@tabler/icons-react";
import { useMemo, useState } from "react";

import { api, ApiError } from "@/lib/api/client";
import type { ContentPackage, Override, TemplateProfile } from "@/lib/api/types";
import { addProjectFiles, patchProjectFile } from "@/lib/state/projects";

export type PictureSource = NonNullable<Override["picture"]>["source"];

interface Props {
  templateId: string | null;
  profile: TemplateProfile | null | undefined;
  pkg: ContentPackage | null | undefined;
  projectId: string | null;
  /** Какие ресурсы шаблона показать первыми: иконки для слота иконки, фото для остальных. */
  preferKind?: "icon" | "photo";
  onPick: (source: PictureSource, previewUrl: string) => void;
  onClose: () => void;
}

const KIND_LABELS: Record<string, string> = { icon: "иконки", photo: "фото", logo: "логотипы", image: "картинки", background: "фоны", screenshot: "скриншоты", mockup: "мокапы", chart_image: "графики", qr: "QR" };

/**
 * Выбор картинки для замены: ресурсы шаблона (по виду и тегам анализа), картинки материалов
 * пакета, загрузка своего файла в проект. Возвращает источник для правки и адрес для холста.
 */
export function AssetPicker({ templateId, profile, pkg, projectId, preferKind, onPick, onClose }: Props) {
  const assets = useMemo(() => (profile?.assets ?? []).filter((a) => a.reusable !== false && a.media_path.startsWith("ppt/media/")), [profile]);
  const kinds = useMemo(() => [...new Set(assets.map((a) => a.kind))].filter((k) => k in KIND_LABELS), [assets]);
  const [kind, setKind] = useState<string>(preferKind && kinds.includes(preferKind) ? preferKind : "all");
  const [query, setQuery] = useState("");
  const [uploading, setUploading] = useState(false);
  const filtered = assets.filter((a) => (kind === "all" || a.kind === kind) && (!query.trim() || (a.tags ?? []).some((t) => t.toLowerCase().includes(query.trim().toLowerCase())) || a.asset_id.includes(query.trim())));
  const packageAssets = pkg?.assets ?? [];

  const upload = async (file: File | null) => {
    if (!file || !projectId) return;
    setUploading(true);
    try {
      const [row] = await addProjectFiles(projectId, [file]);
      if (!row) throw new Error("файл не загружен");
      // Своя картинка для слайда — не материал для импорта: остаётся в проекте как есть.
      patchProjectFile(projectId, row.file_id, { kind: "other" });
      onPick({ kind: "file", file_id: row.file_id, sha256: row.sha256, name: row.name }, URL.createObjectURL(file));
    } catch (e) {
      notifications.show({ color: "red", title: "Картинка не загружена", message: e instanceof ApiError ? e.message : e instanceof Error ? e.message : "" });
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="asset-picker" data-testid="asset-picker">
      <Tabs defaultValue={assets.length ? "template" : packageAssets.length ? "package" : "upload"}>
        <Tabs.List mb="xs">
          <Tabs.Tab value="template" data-testid="asset-tab-template">Из шаблона ({assets.length})</Tabs.Tab>
          {packageAssets.length > 0 && <Tabs.Tab value="package" data-testid="asset-tab-package">Из материалов ({packageAssets.length})</Tabs.Tab>}
          {projectId && <Tabs.Tab value="upload" data-testid="asset-tab-upload">Своя картинка</Tabs.Tab>}
        </Tabs.List>
        <Tabs.Panel value="template">
          <Group gap="xs" mb="xs" wrap="wrap">
            <SegmentedControl size="xs" value={kind} onChange={setKind} data={[{ value: "all", label: "все" }, ...kinds.map((k) => ({ value: k, label: KIND_LABELS[k] ?? k }))]} data-testid="asset-kind" />
            <TextInput size="xs" placeholder="по тегам: рост, деньги, время…" value={query} onChange={(e) => setQuery(e.currentTarget.value)} style={{ width: 220 }} data-testid="asset-search" />
          </Group>
          {filtered.length === 0 ? (
            <Text size="xs" c="dimmed">Подходящих ресурсов в шаблоне нет.</Text>
          ) : (
            <div className="asset-grid" data-testid="asset-grid">
              {filtered.slice(0, 96).map((a) => {
                const url = templateId ? api.templates.mediaUrl(templateId, a.asset_id) : "";
                return (
                  <button
                    key={a.asset_id}
                    type="button"
                    className="asset-item"
                    title={[a.asset_id, ...(a.tags ?? [])].join(" · ")}
                    onClick={() => onPick({ kind: "template", asset_id: a.asset_id, name: a.tags?.[0] ?? a.asset_id }, url)}
                    data-testid={`asset-item-${a.asset_id}`}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={url} alt={a.asset_id} loading="lazy" />
                    <span className="asset-caption">{a.tags?.slice(0, 2).join(", ") || KIND_LABELS[a.kind] || a.kind}</span>
                  </button>
                );
              })}
            </div>
          )}
        </Tabs.Panel>
        {packageAssets.length > 0 && pkg && (
          <Tabs.Panel value="package">
            <div className="asset-grid" data-testid="asset-grid-package">
              {packageAssets.map((a) => {
                const url = api.content.assetUrl(pkg.package_id, a.path);
                return (
                  <button key={a.asset_id} type="button" className="asset-item" title={a.caption ?? a.asset_id} onClick={() => onPick({ kind: "package", asset_id: a.asset_id, name: a.caption ?? a.path.split("/").pop() }, url)} data-testid={`asset-item-${a.asset_id}`}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={url} alt={a.asset_id} loading="lazy" />
                    <span className="asset-caption">{a.caption ?? a.path.split("/").pop()}</span>
                  </button>
                );
              })}
            </div>
          </Tabs.Panel>
        )}
        {projectId && (
          <Tabs.Panel value="upload">
            <Stack gap="xs">
              <Text size="xs" c="dimmed">PNG или JPEG: файл сохраняется в проекте и подставляется на слайд с обрезкой по рамке.</Text>
              <FileButton onChange={(f) => void upload(f)} accept="image/png,image/jpeg">
                {(props) => (
                  <Button {...props} size="xs" variant="default" leftSection={uploading ? <Loader size={12} /> : <IconUpload size={14} />} disabled={uploading} data-testid="asset-upload">
                    Выбрать файл
                  </Button>
                )}
              </FileButton>
            </Stack>
          </Tabs.Panel>
        )}
      </Tabs>
      <Group justify="space-between" mt="xs">
        <Badge size="xs" variant="light" color="gray">только ресурсы шаблона и материалов: внешние библиотеки не подключены</Badge>
        <Button size="compact-xs" variant="subtle" color="gray" onClick={onClose} data-testid="asset-picker-close">Закрыть</Button>
      </Group>
    </div>
  );
}
