"use client";

import { ActionIcon, Group, Tabs, Text, Tooltip } from "@mantine/core";
import { Dropzone } from "@mantine/dropzone";
import { IconFile, IconFileTypePpt, IconPhoto, IconTable, IconTrash, IconUpload, IconVideo } from "@tabler/icons-react";
import { useRef, useState } from "react";

import { api, type TemplateDetail } from "@/lib/api/client";
import { usePolling } from "@/lib/api/usePolling";
import { formatBytes, plural } from "@/lib/format";
import { dragProps } from "@/lib/state/drag";
import type { Project, ProjectFile } from "@/lib/state/projects";

import { TemplateAssets, templateAssets } from "./TemplateAssets";
import { TemplateSlides, templateSlides } from "./TemplateSlides";

interface Props {
  project: Project;
  onAdd: (files: File[]) => void;
  onRemove: (fileId: string) => void;
}

type FileType = "presentation" | "document" | "table" | "image" | "video" | "other";

const TYPE_LABEL: Record<FileType, string> = { presentation: "Презентации", document: "Документы", table: "Таблицы", image: "Изображения", video: "Видео", other: "Другое" };
// Презентаций в сетке нет: шаблон выбирают вверху справа, готовая презентация открыта в
// редакторе, а тащить PPTX целиком некуда. Их готовые слайды — на вкладке «Слайды».
const TYPE_ORDER: FileType[] = ["document", "table", "image", "video", "other"];

function typeOf(f: ProjectFile): FileType {
  if (/\.pptx$/i.test(f.name)) return "presentation";
  if (/\.(docx|pdf|md|txt)$/i.test(f.name)) return "document";
  if (/\.(xlsx|csv)$/i.test(f.name)) return "table";
  if (/\.(png|jpe?g|gif|webp|svg)$/i.test(f.name)) return "image";
  if (/\.(mp4|mov|webm)$/i.test(f.name)) return "video";
  return "other";
}

function icon(type: FileType, size = 18) {
  if (type === "presentation") return <IconFileTypePpt size={size} stroke={1.5} />;
  if (type === "table") return <IconTable size={size} stroke={1.5} />;
  if (type === "image") return <IconPhoto size={size} stroke={1.5} />;
  if (type === "video") return <IconVideo size={size} stroke={1.5} />;
  return <IconFile size={size} stroke={1.5} />;
}

/** Файлы вкладки «Загруженные» и счётчика «Файлы»: без презентаций и файла шаблона. */
export const listedFiles = (files: ProjectFile[]) => files.filter((f) => typeOf(f) !== "presentation" && f.kind !== "template");

/** Миниатюру сервер строит для картинок и PDF; остальным сразу значок типа. */
const hasThumbnail = (f: ProjectFile) => f.check.status === "ok" && ["image", "pdf"].includes(f.check.format ?? "");
/** На слайд встают проверенные PNG и JPEG. */
const insertable = (f: ProjectFile) => f.check.status === "ok" && f.check.format === "image" && /\.(png|jpe?g)$/i.test(f.name);

type Tab = "uploaded" | "template" | "slides";

/**
 * Файлы проекта сеткой карточек: загруженные по типам, ресурсы шаблона (картинки, иконки,
 * логотипы из профиля) и его готовые слайды. Готовый PPTX скачивается только из редактора.
 */
export function FilesPanel({ project, onAdd, onRemove }: Props) {
  const openRef = useRef<() => void>(null);
  const [tab, setTab] = useState<Tab>("uploaded");
  const files = listedFiles(project.files);
  const groups = TYPE_ORDER.map((type) => ({ type, files: files.filter((f) => typeOf(f) === type) })).filter((g) => g.files.length > 0);
  // Профиль шаблона — один запрос на обе вкладки шаблона и их счётчики. Ответ помечен своим
  // шаблоном: после смены шаблона прежний профиль не показывается, пока не пришёл новый.
  const templateId = project.template_id;
  const loaded = usePolling<{ id: string; detail: TemplateDetail }>(
    templateId ? () => api.templates.get(templateId).then((detail) => ({ id: templateId, detail })) : null,
    (d) => d.detail.status === "succeeded" || d.detail.status === "failed",
    [templateId],
  );
  const template = templateId && loaded.data?.id === templateId ? loaded.data.detail : null;
  const analyzed = template?.status === "succeeded";
  const assetCount = analyzed ? templateAssets(template).length : 0;
  const slideCount = analyzed ? templateSlides(template).length : 0;

  const role = (f: ProjectFile): { text: string; active: boolean } => {
    if (f.kind === "material") return f.package_id === project.package_id ? { text: "в содержании", active: true } : f.package_id ? { text: "переимпортирован", active: false } : { text: "ожидает импорта", active: false };
    return { text: "не используется при генерации", active: false };
  };

  return (
    <Dropzone
      onDrop={onAdd}
      openRef={openRef}
      activateOnClick={false}
      styles={{ root: { border: 0, padding: 0, background: "transparent", borderRadius: 0, display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }, inner: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0, pointerEvents: "auto" } }}
      data-testid="files-dropzone"
    >
      <div className="editor-panel-head files-head">
        <Group justify="space-between" align="baseline">
          <Text fw={600} size="lg">Файлы проекта</Text>
          <Text size="xs" c="dimmed">{files.length ? `${files.length} ${plural(files.length, "файл", "файла", "файлов")}` : ""}</Text>
        </Group>
        <Tabs value={tab} onChange={(v) => setTab((v as Tab) ?? "uploaded")} mt="xs">
          <Tabs.List>
            <Tabs.Tab value="uploaded" data-testid="files-tab-uploaded">Загруженные{files.length ? ` · ${files.length}` : ""}</Tabs.Tab>
            <Tabs.Tab value="template" data-testid="files-tab-template">Из шаблона{assetCount ? ` · ${assetCount}` : ""}</Tabs.Tab>
            <Tabs.Tab value="slides" data-testid="files-tab-slides">Слайды{slideCount ? ` · ${slideCount}` : ""}</Tabs.Tab>
          </Tabs.List>
        </Tabs>
      </div>
      <div className="editor-panel-scroll files-scroll" data-testid="files-panel">
        {/* Вкладка ресурсов остаётся смонтированной: фильтр и поиск переживают переключение. */}
        <TemplateAssets templateId={templateId} detail={template} hidden={tab !== "template"} />
        {tab === "slides" && <TemplateSlides templateId={templateId} detail={template} />}
        {tab === "uploaded" && <>
          {groups.map((g) => (
            <section key={g.type} className="files-group">
              <Text size="xs" c="dimmed" fw={500} mb={6}>{TYPE_LABEL[g.type]} · {g.files.length}</Text>
              <div className="file-grid">
                {g.files.map((f) => (
                  <FileCard key={f.file_id} projectId={project.project_id} file={f} type={g.type} role={role(f)} onRemove={() => onRemove(f.file_id)} />
                ))}
              </div>
            </section>
          ))}

          <button type="button" className="files-drop-hint" onClick={() => openRef.current?.()} data-testid="files-add">
            <IconUpload size={18} stroke={1.5} />
            <span>{files.length ? "Перетащите сюда ещё файлы или нажмите, чтобы выбрать" : "Перетащите сюда файл или нажмите, чтобы выбрать"}</span>
          </button>
        </>}
      </div>
    </Dropzone>
  );
}

function FileCard({ projectId, file, type, role, onRemove }: {
  projectId: string;
  file: ProjectFile;
  type: FileType;
  role: { text: string; active: boolean };
  onRemove: () => void;
}) {
  // Миниатюры может не быть (обложки нет, байты удалены) — тогда значок типа.
  const [broken, setBroken] = useState(false);
  const thumb = hasThumbnail(file) && !broken;
  const drag = insertable(file) ? dragProps({ project_id: projectId, file_id: file.file_id, name: file.name }) : {};
  return (
    <div className="file-card" data-testid={`file-${file.file_id}`} title={insertable(file) ? `${file.name} — перетащите на слайд` : file.name} {...drag}>
      <a className="file-thumb" href={api.projects.fileUrl(projectId, file.file_id)} target="_blank" rel="noreferrer" draggable={false} aria-label={`Открыть ${file.name}`}>
        {thumb
          // eslint-disable-next-line @next/next/no-img-element
          ? <img src={api.projects.thumbnailUrl(projectId, file.file_id)} alt="" loading="lazy" draggable={false} onError={() => setBroken(true)} />
          : <span className="file-thumb-icon">{icon(type, 28)}</span>}
      </a>
      <div className="file-meta">
        <Text size="xs" fw={500} truncate>{file.name}</Text>
        <Text size="xs" c={role.active ? "green.8" : "dimmed"} truncate>{role.text} · {formatBytes(file.size_bytes)}</Text>
      </div>
      <div className="file-actions">
        <Tooltip label="Удалить из проекта">
          <ActionIcon variant="default" size="sm" onClick={onRemove} aria-label="Удалить" data-testid={`file-remove-${file.file_id}`}><IconTrash size={14} /></ActionIcon>
        </Tooltip>
      </div>
    </div>
  );
}
