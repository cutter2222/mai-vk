"use client";

import { ActionIcon, Group, Tabs, Text, Tooltip } from "@mantine/core";
import { Dropzone } from "@mantine/dropzone";
import { IconFile, IconFileTypePpt, IconPhoto, IconTable, IconTrash, IconUpload, IconVideo } from "@tabler/icons-react";
import { useRef, useState } from "react";

import { api } from "@/lib/api/client";
import { formatBytes } from "@/lib/format";
import { imageDragProps } from "@/lib/state/drag";
import type { Project, ProjectFile } from "@/lib/state/projects";

import { TemplateAssets } from "./TemplateAssets";

interface Props {
  project: Project;
  onAdd: (files: File[]) => void;
  onRemove: (fileId: string) => void;
  onSelectTemplate: (templateId: string, name: string) => void;
}

type FileType = "presentation" | "document" | "table" | "image" | "video" | "other";

const TYPE_LABEL: Record<FileType, string> = { presentation: "Презентации", document: "Документы", table: "Таблицы", image: "Изображения", video: "Видео", other: "Другое" };
const TYPE_ORDER: FileType[] = ["presentation", "document", "table", "image", "video", "other"];

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

/** Миниатюру сервер строит для картинок, PDF и PPTX; остальным сразу значок типа. */
const hasThumbnail = (f: ProjectFile) => f.check.status === "ok" && ["image", "pdf", "pptx"].includes(f.check.format ?? "");
/** На слайд встают проверенные PNG и JPEG. */
const insertable = (f: ProjectFile) => f.check.status === "ok" && f.check.format === "image" && /\.(png|jpe?g)$/i.test(f.name);

type Tab = "uploaded" | "template";

/**
 * Файлы проекта сеткой карточек: загруженные по типам и ресурсы шаблона (картинки, иконки,
 * логотипы из профиля). Готовый PPTX скачивается только из редактора.
 */
export function FilesPanel({ project, onAdd, onRemove, onSelectTemplate }: Props) {
  const openRef = useRef<() => void>(null);
  const [tab, setTab] = useState<Tab>("uploaded");
  const [templateCount, setTemplateCount] = useState<number | null>(null);
  const groups = TYPE_ORDER.map((type) => ({ type, files: project.files.filter((f) => typeOf(f) === type) })).filter((g) => g.files.length > 0);

  const role = (f: ProjectFile): { text: string; active: boolean } => {
    if (f.kind === "template") return f.template_id === project.template_id ? { text: "шаблон проекта", active: true } : f.template_id ? { text: "в библиотеке шаблонов", active: false } : { text: "не загружен", active: false };
    if (f.kind === "material") return f.package_id === project.package_id ? { text: "в содержании", active: true } : f.package_id ? { text: "переимпортирован", active: false } : { text: "ожидает импорта", active: false };
    return typeOf(f) === "presentation" ? { text: "ждёт ответа в чате", active: false } : { text: "не используется при генерации", active: false };
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
          <Text size="xs" c="dimmed">{project.files.length ? `${project.files.length} файлов` : ""}</Text>
        </Group>
        <Tabs value={tab} onChange={(v) => setTab((v as Tab) ?? "uploaded")} mt="xs">
          <Tabs.List>
            <Tabs.Tab value="uploaded" data-testid="files-tab-uploaded">Загруженные{project.files.length ? ` · ${project.files.length}` : ""}</Tabs.Tab>
            <Tabs.Tab value="template" data-testid="files-tab-template">Из шаблона{templateCount ? ` · ${templateCount}` : ""}</Tabs.Tab>
          </Tabs.List>
        </Tabs>
      </div>
      <div className="editor-panel-scroll files-scroll" data-testid="files-panel">
        {/* Ресурсы шаблона загружаются и тогда, когда вкладка скрыта: её счётчик виден сразу. */}
        <TemplateAssets templateId={project.template_id} hidden={tab !== "template"} onCount={setTemplateCount} />
        {tab === "uploaded" && <>
          {groups.map((g) => (
            <section key={g.type} className="files-group">
              <Text size="xs" c="dimmed" fw={500} mb={6}>{TYPE_LABEL[g.type]} · {g.files.length}</Text>
              <div className="file-grid">
                {g.files.map((f) => (
                  <FileCard key={f.file_id} projectId={project.project_id} file={f} type={g.type} role={role(f)}
                    onRemove={() => onRemove(f.file_id)}
                    onSelectTemplate={f.kind === "template" && f.template_id && f.template_id !== project.template_id ? () => onSelectTemplate(f.template_id as string, f.name) : undefined} />
                ))}
              </div>
            </section>
          ))}

          <button type="button" className="files-drop-hint" onClick={() => openRef.current?.()} data-testid="files-add">
            <IconUpload size={18} stroke={1.5} />
            <span>{project.files.length ? "Перетащите сюда ещё файлы или нажмите, чтобы выбрать" : "Перетащите сюда шаблон PPTX, документы, таблицы или картинки"}</span>
          </button>
        </>}
      </div>
    </Dropzone>
  );
}

function FileCard({ projectId, file, type, role, onRemove, onSelectTemplate }: {
  projectId: string;
  file: ProjectFile;
  type: FileType;
  role: { text: string; active: boolean };
  onRemove: () => void;
  onSelectTemplate?: () => void;
}) {
  // Миниатюры может не быть (обложки нет, байты удалены) — тогда значок типа.
  const [broken, setBroken] = useState(false);
  const thumb = hasThumbnail(file) && !broken;
  const drag = insertable(file) ? imageDragProps({ project_id: projectId, file_id: file.file_id, name: file.name }) : {};
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
        {onSelectTemplate && (
          <Tooltip label="Сделать шаблоном проекта">
            <ActionIcon variant="default" size="sm" onClick={onSelectTemplate} aria-label="Использовать шаблон"><IconFileTypePpt size={14} /></ActionIcon>
          </Tooltip>
        )}
        <Tooltip label="Удалить из проекта">
          <ActionIcon variant="default" size="sm" onClick={onRemove} aria-label="Удалить" data-testid={`file-remove-${file.file_id}`}><IconTrash size={14} /></ActionIcon>
        </Tooltip>
      </div>
    </div>
  );
}
