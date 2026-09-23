"use client";

import { Alert, Button, Group, Loader, Menu, Select, Stack, Text } from "@mantine/core";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { createPortal } from "react-dom";

import { OfficeEditor, type OfficeEditHandle } from "@/components/office/OfficeEditor";
import { api, type OfficeDocument, type OfficePreview, type OfficeObject, type OfficeSelection, type TemplateDetail } from "@/lib/api/client";
import { selectOfficeObject } from "@/lib/editor/officeSelection";
import { downloadArtifact } from "@/lib/download";
import { VARIANT_LABELS } from "@/lib/format";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";
import { SlideViewer } from "../preview/SlideViewer";

/** Saved office bytes are the source of previews and AI edits, not stale generation artifacts. */
export function ProjectOffice({ session, title, projectId, editRef, actionsTarget, selection, onSelectionChange, template }: {
  session: GenerationSession; title: string; projectId: string;
  editRef?: Ref<OfficeEditHandle>; actionsTarget?: HTMLElement | null;
  selection: OfficeSelection | null;
  onSelectionChange: (value: OfficeSelection | null) => void;
  template?: { id: string; detail: TemplateDetail };
}) {
  const params = useSearchParams();
  const router = useRouter();
  const variant = session.variant;
  const latest = session.jobId && variant?.artifacts?.pptx
    ? { jobId: session.jobId, artifact: variant.artifacts.pptx }
    : null;
  // Parent mounts only once both the job and its first PPTX exist; later publications
  // must not reset this source. Return parameters pin a non-default/older artifact.
  const [source, setSource] = useState(() => !template && params.get("officeJob") && params.get("officeArtifact")
    ? { jobId: params.get("officeJob")!, artifact: params.get("officeArtifact")! }
    : latest ?? { jobId: "", artifact: "" });
  const [doc, setDoc] = useState<OfficeDocument | null>(null);
  // The editor lives in Slides; the saved preview remains available for scoped AI selection.
  const [manual, setManual] = useState(() => params.get("officeView") !== "preview");
  const manualEdit = useRef<OfficeEditHandle>(null);
  const saved = useCallback((value: OfficeDocument) => {
    setDoc(value);
    setManual(false);
    onSelectionChange(null);
  }, [onSelectionChange]);
  const [preview, setPreview] = useState<OfficePreview | null>(null);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const [index, setIndex] = useState(0);
  const [editing, setEditing] = useState(false);
  const [multiple, setMultiple] = useState(false);
  const [objectMap, setObjectMap] = useState<{ documentId: string; revision: number; objects: OfficeObject[] } | null>(null);
  const [objectError, setObjectError] = useState("");
  const [objectAttempt, setObjectAttempt] = useState(0);
  const busy = useRef(false);
  const changed = latest && (source.jobId !== latest.jobId || source.artifact !== latest.artifact);
  const templateId = template?.id;
  const templatePreviews = (template?.detail.previews ?? []).filter((path) => /(^|\/)slide-\d+\.png$/.test(path))
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));

  useEffect(() => {
    let cancelled = false;
    const opening = templateId ? api.office.templateCopy(projectId, templateId) : api.office.create(source.jobId, source.artifact);
    void opening.then((value) => {
      if (!cancelled) setDoc(value);
    }).catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [source.jobId, source.artifact, attempt, projectId, templateId]);

  const id = doc?.id;
  const revision = doc?.revision;
  useEffect(() => { onSelectionChange(null); }, [id, revision, index, onSelectionChange]);
  useEffect(() => {
    if (manual || !id || revision === undefined) return;
    let cancelled = false;
    void api.office.objects(id, revision).then((value) => {
      if (!cancelled) { setObjectMap({ ...value, documentId: id }); setObjectError(""); }
    }).catch((e: Error) => { if (!cancelled) setObjectError(e.message); });
    return () => { cancelled = true; };
  }, [manual, id, revision, objectAttempt]);
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await api.office.get(id);
        if (!cancelled) {
          setDoc((old) => old && old.revision > value.revision ? old : value);
          setPollError("");
        }
      } catch (e) { if (!cancelled) setPollError(e instanceof Error ? e.message : "Сервер недоступен"); }
      finally { if (!cancelled) timer = setTimeout(poll, 2000); }
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [id]);

  useEffect(() => {
    if (manual || !id || revision === undefined) return;
    if (templateId && revision === 0) return;
    let cancelled = false;
    void api.office.preview(id, revision).then((value) => {
      if (!cancelled) { setPreview(value); setPreviewError(""); }
    }).catch((e: Error) => { if (!cancelled) setPreviewError(e.message); });
    return () => { cancelled = true; };
  }, [manual, id, revision, previewAttempt, templateId]);

  // Revision zero is byte-identical to the template: reuse its already rendered real slides.
  const visiblePreview = templateId && doc?.revision === 0
    ? { revision: 0, slides: templatePreviews, ratio: undefined }
    : preview;
  const visibleRevision = visiblePreview?.revision;

  useImperativeHandle(editRef, () => ({ edit: async (instruction, target) => {
    if (manual) {
      if (target) throw new Error("Для правки выбранных объектов откройте сохранённое превью и выберите их заново.");
      if (!manualEdit.current) throw new Error("Дождитесь загрузки редактора.");
      return manualEdit.current.edit(instruction);
    }
    if (!doc || pollError) throw new Error("Дождитесь проверки сохранённой презентации.");
    if (busy.current) throw new Error("Предыдущая ИИ-правка ещё выполняется.");
    if (doc.active_key || doc.error) throw new Error("Завершите ручное редактирование во всех вкладках и дождитесь сохранения.");
    if (visibleRevision !== doc.revision) throw new Error("Дождитесь актуального превью перед ИИ-правкой.");
    if (target && (target.documentId !== doc.id || target.revision !== doc.revision || target.slide !== index + 1)) throw new Error("Выбранный объект устарел. Выберите его заново.");
    busy.current = true;
    setEditing(true);
    try {
      const targets = target?.objects.map(({ slide, shape_id }) => ({ slide, shape_id }));
      const result = await api.office.edit(doc.id, doc.revision, instruction, targets?.length === 1 ? targets[0] : targets);
      setDoc(result.document);
      setPreviewError("");
      return result.changed ? `Правка сохранена в этом PPTX · v${result.document.revision}. ${result.message}` : `Документ не изменён. ${result.message}`;
    } finally { busy.current = false; setEditing(false); }
  } }), [manual, doc, pollError, visibleRevision, index]);

  const selectable = !manual && !editing && !pollError && !doc?.active_key && !doc?.error && visiblePreview?.revision === doc?.revision && objectMap?.documentId === doc?.id && objectMap?.revision === doc?.revision;
  const currentObjects = selectable ? objectMap?.objects.filter((obj) => obj.slide === index + 1) ?? [] : [];

  const retry = () => { setError(""); setPreviewError(""); setAttempt((n) => n + 1); };
  const actions = doc && <Group gap="xs">
    {(session.result?.variants.length ?? 0) > 1 && <Select size="xs" aria-label="Вариант презентации" value={session.selectedVariant}
      disabled={editing || Boolean(doc.active_key) || Boolean(doc.error) || Boolean(pollError)}
      onChange={(value) => { if (value) session.setSelectedVariant(value); }}
      data={(session.result?.variants ?? []).filter((v) => v.artifacts?.pptx).map((v) => ({ value: v.variant_id, label: VARIANT_LABELS[v.variant_id] ?? v.variant_id }))} />}
    <Button disabled={editing} onClick={() => { onSelectionChange(null); setManual(true); }} data-testid="edit-slides">Редактировать слайды</Button>
    <Button variant="subtle" component={Link} href={`/office?${new URLSearchParams({ id: doc.id, project: projectId, ...(templateId ? {} : { officeJob: source.jobId, officeArtifact: source.artifact }) })}`} disabled={editing} data-testid="open-office">На весь экран</Button>
    <Menu>
      <Menu.Target><Button variant="light" data-testid="download-menu">Скачать</Button></Menu.Target>
      <Menu.Dropdown>{(["pptx", "pdf", "html"] as const).map((format) => <Menu.Item key={format} data-testid={`dl-${format}`} onClick={() => {
        void downloadArtifact(api.office.downloadUrl(doc.id, doc.revision, format), `${title}-v${doc.revision}.${format}`).catch((e: Error) => setError(e.message));
      }}>{format.toUpperCase()}</Menu.Item>)}</Menu.Dropdown>
    </Menu>
  </Group>;

  return <div className="project-office" data-testid="project-office">
    {!manual && (actionsTarget ? createPortal(actions, actionsTarget) : actions)}
    {templateId && <Text size="xs" c="dimmed" p="xs">Рабочая копия шаблона · ИИ-правки сохраняются только в этом проекте. Исходный шаблон не изменяется.</Text>}
    {!templateId && !session.terminal && <Text size="xs" c="dimmed" p="xs" role="status">Готовый вариант уже доступен. Остальные варианты и проверки выполняются в фоне; этот PPTX не заменяется.</Text>}
    {changed && <Alert color="blue" title="Доступна другая версия презентации">
      Ручные и ИИ-правки остаются в текущем PPTX и не переносятся в другую сборку.
      <Button ml="sm" size="xs" disabled={manual || editing || !doc || Boolean(doc.active_key) || Boolean(doc.error) || Boolean(pollError)} onClick={() => {
        if (!latest) return;
        setDoc(null); setPreview(null); setError(""); setPreviewError(""); setIndex(0); setSource(latest);
        router.replace(`/project?${new URLSearchParams({ id: projectId, officeJob: latest.jobId, officeArtifact: latest.artifact })}`);
      }}>Открыть выбранную версию</Button>
    </Alert>}
    {!manual && doc?.active_key && <Alert color="yellow">Ручная сессия ещё открыта или сохраняется. Показана последняя серверная версия; ИИ-правки заблокированы до завершения сессии.</Alert>}
    {pollError && <Alert color="yellow">Сохранение не подтверждено: {pollError}</Alert>}
    {doc?.error && <Alert color="red">{doc.error}</Alert>}
    {error && <Alert color="red">{error}<Button ml="sm" size="xs" onClick={retry}>Повторить открытие</Button></Alert>}
    {editing && <Alert color="blue">ИИ применяет правку PPTX. После сохранения превью обновится.</Alert>}
    {objectError && <Alert color="yellow">Выбор объектов недоступен: {objectError}<Button size="xs" ml="sm" onClick={() => setObjectAttempt((n) => n + 1)}>Повторить загрузку объектов</Button></Alert>}
    {currentObjects.length > 0 && <Group px="xs" gap="xs">
      <Button size="xs" variant={multiple ? "filled" : "light"} aria-pressed={multiple} onClick={() => setMultiple((value) => !value)} data-testid="multi-select">Мультивыделение</Button>
      <Text size="xs" c="dimmed">Клик — выбрать; Shift/Ctrl/⌘ + клик — добавить или убрать. Затем напишите команду в чат.</Text>
      {selection && <Button size="xs" variant="subtle" onClick={() => onSelectionChange(null)}>Снять выделение ({selection.objects.length})</Button>}
    </Group>}
    {!manual && previewError && <Alert color="red">{previewError}<Button ml="sm" size="xs" onClick={() => { setPreviewError(""); setPreviewAttempt((n) => n + 1); }}>Повторить превью</Button></Alert>}
    {manual ? (doc ? <OfficeEditor key={doc.id} id={doc.id} title={title} embedded editRef={manualEdit} actionsTarget={actionsTarget} onSaved={saved} />
      : !error && <Stack align="center" justify="center" flex={1}><Loader size="sm" /><Text size="sm">Открываем редактор слайдов…</Text></Stack>) : doc && visiblePreview ? <>
      {visiblePreview.revision !== doc.revision && <Text p="xs" size="sm" role="status">Обновляем превью v{doc.revision}; пока показана v{visiblePreview.revision}.</Text>}
      <SlideViewer index={index} onIndex={setIndex} ratio={visiblePreview.ratio} caption={<Text size="xs">Превью · v{visiblePreview.revision}</Text>}
        outlines={currentObjects.map((obj) => ({ ...obj, id: obj.shape_id, selected: selection?.documentId === doc.id && selection.revision === doc.revision && selection.objects.some((item) => item.slide === obj.slide && item.shape_id === obj.shape_id) }))}
        onClearSelection={() => onSelectionChange(null)}
        onOutlineClick={(shapeId, additive) => {
          const obj = currentObjects.find((value) => value.shape_id === shapeId);
          if (obj) onSelectionChange(selectOfficeObject(doc, obj, selection, additive || multiple));
        }}
        slides={visiblePreview.slides.map((name, i) => ({ key: `${visiblePreview.revision}-${name}`, deckIndex: i, label: `Слайд ${i + 1}`, src: templateId && visiblePreview.revision === 0 ? api.templates.assetUrl(templateId, name) : api.office.previewUrl(doc.id, visiblePreview.revision, name) }))} />
    </> : !error && !previewError && <Stack align="center" justify="center" flex={1}><Loader size="sm" /><Text size="sm" c="dimmed">Формируем превью сохранённого PPTX…</Text></Stack>}
  </div>;
}