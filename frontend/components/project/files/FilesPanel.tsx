"use client";

import { ActionIcon, Group, Menu, Text, Tooltip } from "@mantine/core";
import { Dropzone } from "@mantine/dropzone";
import { notifications } from "@mantine/notifications";
import { IconDownload, IconFile, IconFileTypePpt, IconPhoto, IconTable, IconTrash, IconUpload, IconVideo } from "@tabler/icons-react";
import { useRef } from "react";

import { api, ApiError } from "@/lib/api/client";
import { downloadArtifact } from "@/lib/download";
import { formatBytes, VARIANT_LABELS } from "@/lib/format";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";
import { fileStore } from "@/lib/state/fileStore";
import type { Project, ProjectFile } from "@/lib/state/projects";

interface Props {
  project: Project;
  session: GenerationSession;
  onAdd: (files: File[]) => void;
  onRemove: (fileId: string) => void;
  onSelectTemplate: (templateId: string) => void;
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

function icon(type: FileType) {
  if (type === "presentation") return <IconFileTypePpt size={18} stroke={1.5} />;
  if (type === "table") return <IconTable size={18} stroke={1.5} />;
  if (type === "image") return <IconPhoto size={18} stroke={1.5} />;
  if (type === "video") return <IconVideo size={18} stroke={1.5} />;
  return <IconFile size={18} stroke={1.5} />;
}

/** Всё, что лежит в проекте, по типам, и собранные сервисом файлы. Вся панель принимает перетаскивание. */
export function FilesPanel({ project, session, onAdd, onRemove, onSelectTemplate }: Props) {
  const openRef = useRef<() => void>(null);
  const groups = TYPE_ORDER.map((type) => ({ type, files: project.files.filter((f) => typeOf(f) === type) })).filter((g) => g.files.length > 0);
  const result = session.result;

  const role = (f: ProjectFile): { text: string; active: boolean } => {
    if (f.kind === "template") return f.template_id === project.template_id ? { text: "шаблон проекта", active: true } : f.template_id ? { text: "в библиотеке шаблонов", active: false } : { text: "не загружен", active: false };
    if (f.kind === "material") return f.package_id === project.package_id ? { text: "в содержании", active: true } : f.package_id ? { text: "переимпортирован", active: false } : { text: "ожидает импорта", active: false };
    return typeOf(f) === "presentation" ? { text: "ждёт ответа в чате", active: false } : { text: "не используется при генерации", active: false };
  };

  const download = (name: string, label: string) => async () => {
    if (!session.jobId) return;
    try {
      await downloadArtifact(api.generations.artifactUrl(session.jobId, name), label);
    } catch (e) {
      notifications.show({ color: "red", title: "Файл не скачан", message: e instanceof ApiError ? e.message : "Неизвестная ошибка" });
    }
  };

  return (
    <Dropzone
      onDrop={onAdd}
      openRef={openRef}
      activateOnClick={false}
      styles={{ root: { border: 0, padding: 0, background: "transparent", borderRadius: 0, display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }, inner: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0, pointerEvents: "auto" } }}
      data-testid="files-dropzone"
    >
      <div className="editor-panel-head">
        <Group justify="space-between" align="baseline">
          <Text fw={600} size="lg">Файлы проекта</Text>
          <Text size="xs" c="dimmed">{project.files.length ? `${project.files.length} файлов` : ""}</Text>
        </Group>
      </div>
      <div className="editor-panel-scroll files-scroll" data-testid="files-panel">
        {groups.map((g) => (
          <section key={g.type} className="files-group">
            <Text size="xs" c="dimmed" fw={500} mb={4}>{TYPE_LABEL[g.type]} · {g.files.length}</Text>
            {g.files.map((f) => {
              const r = role(f);
              const lost = !fileStore.has(f.id) && !f.template_id && !f.package_id;
              return (
                <div key={f.id} className="file-row" data-testid={`file-${f.id}`}>
                  <span className="file-icon">{icon(g.type)}</span>
                  <div className="file-main">
                    <Text size="sm" truncate title={f.name}>{f.name}</Text>
                    <Text size="xs" c={r.active ? "green.8" : "dimmed"} truncate>
                      {r.text} · {formatBytes(f.size)}{lost ? " · содержимое не сохранилось после перезагрузки" : ""}
                    </Text>
                  </div>
                  <div className="file-actions">
                    {f.kind === "template" && f.template_id && f.template_id !== project.template_id && (
                      <Tooltip label="Сделать шаблоном проекта">
                        <ActionIcon variant="subtle" color="gray" size="sm" onClick={() => onSelectTemplate(f.template_id as string)} aria-label="Использовать шаблон"><IconFileTypePpt size={14} /></ActionIcon>
                      </Tooltip>
                    )}
                    <Tooltip label="Удалить из проекта">
                      <ActionIcon variant="subtle" color="gray" size="sm" onClick={() => onRemove(f.id)} aria-label="Удалить" data-testid={`file-remove-${f.id}`}><IconTrash size={14} /></ActionIcon>
                    </Tooltip>
                  </div>
                </div>
              );
            })}
          </section>
        ))}

        {result && (
          <section className="files-group">
            <Text size="xs" c="dimmed" fw={500} mb={4}>Собрано сервисом · {result.variants.length}</Text>
            {result.variants.map((v) => {
              const a = v.artifacts;
              const ready = Boolean(a?.pptx);
              return (
                <div key={v.variant_id} className="file-row">
                  <span className="file-icon"><IconFileTypePpt size={18} stroke={1.5} /></span>
                  <div className="file-main">
                    <Text size="sm">{VARIANT_LABELS[v.variant_id] ?? v.variant_id} · ревизия {v.revision}</Text>
                    <Text size="xs" c="dimmed">{ready ? `${v.slide_count ?? a?.thumbnails?.length ?? 0} слайдов · PPTX, PDF, HTML` : v.status === "failed" ? "не собран" : "собирается"}</Text>
                  </div>
                  <div className="file-actions" data-always>
                    <Menu withinPortal position="bottom-end" disabled={!ready}>
                      <Menu.Target>
                        <ActionIcon variant="subtle" color="gray" size="sm" disabled={!ready} aria-label="Скачать"><IconDownload size={14} /></ActionIcon>
                      </Menu.Target>
                      <Menu.Dropdown>
                        {a?.pptx && <Menu.Item onClick={download(a.pptx, `${project.title}-${v.variant_id}.pptx`)}>PPTX</Menu.Item>}
                        {a?.pdf && <Menu.Item onClick={download(a.pdf, `${project.title}-${v.variant_id}.pdf`)}>PDF</Menu.Item>}
                        {a?.html && <Menu.Item onClick={download(a.html, `${project.title}-${v.variant_id}.html`)}>HTML</Menu.Item>}
                      </Menu.Dropdown>
                    </Menu>
                  </div>
                </div>
              );
            })}
          </section>
        )}

        <button type="button" className="files-drop-hint" onClick={() => openRef.current?.()} data-testid="files-add">
          <IconUpload size={18} stroke={1.5} />
          <span>{project.files.length ? "Перетащите сюда ещё файлы или нажмите, чтобы выбрать" : "Перетащите сюда шаблон PPTX, документы, таблицы или картинки"}</span>
        </button>
      </div>
    </Dropzone>
  );
}
