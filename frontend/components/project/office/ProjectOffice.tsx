"use client";

import { Alert, Button, Group, Loader, Menu, SegmentedControl, Stack, Text } from "@mantine/core";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { createPortal } from "react-dom";
import { notifications } from "@mantine/notifications";

import { OfficeEditor, outcomeOf, type OfficeEditHandle } from "@/components/office/OfficeEditor";
import { api, type OfficeApplySlide, type OfficeDocument, type OfficePreview, type OfficeObject, type OfficeSelection, type TemplateDetail } from "@/lib/api/client";
import type { LiveSelection } from "@/lib/editor/officeLive";
import { selectOfficeObject } from "@/lib/editor/officeSelection";
import { downloadArtifact } from "@/lib/download";
import { VARIANT_LABELS } from "@/lib/format";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";
import { setOpenDocument } from "@/lib/state/projects";
import { slideNumbers } from "../chat/routeRunner";
import { SlideViewer } from "../preview/SlideViewer";

/** «balanced/r2/deck.pptx» → вариант и номер ревизии артефакта. */
function revisionOf(artifact: string): { variant: string; revision: number } | null {
  const match = /^([^/]+)\/r(\d+)\//.exec(artifact);
  return match ? { variant: match[1], revision: Number(match[2]) } : null;
}

/** Saved office bytes are the source of previews and AI edits, not stale generation artifacts. */
export function ProjectOffice({ session, title, projectId, editRef, actionsTarget, selection, onSelectionChange, template, onEditorReady, onLiveSelection }: {
  session: GenerationSession; title: string; projectId: string;
  /** Текущий слайд и выделение живого редактора — адресат сообщения в чате. */
  onLiveSelection?: (value: LiveSelection | null) => void;
  /** Редактор открыл документ: слайды видны. */
  onEditorReady?: () => void;
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
  // Ревизия варианта, чей пересобранный слайд уже перенесён в открытую копию: копия ей
  // соответствует, и баннер «Доступна другая версия» о ней не нужен.
  const [appliedArtifact, setAppliedArtifact] = useState<string | null>(null);
  const changed = latest && (source.jobId !== latest.jobId || source.artifact !== latest.artifact) && latest.artifact !== appliedArtifact;
  // Правил ли человек открытый документ. Пока нет — новая версия (диаграммы готовой
  // презентации стали редактируемыми, правка из чата, повтор сборки) открывается сама: терять
  // нечего. После любой правки остаётся баннер «Доступна другая версия». Сохранённые версии
  // документа (revision > 0) — тоже правки; ключ открытой сессии ONLYOFFICE ставит всегда,
  // поэтому признаком правки служит сигнал редактора о несохранённых изменениях.
  const [touched, setTouched] = useState(false);
  const onModified = useCallback((value: boolean) => { if (value) setTouched(true); }, []);
  const [prevSource, setPrevSource] = useState(source);
  // Правки из чата, которые уже были, когда копия открылась: их слайды в ней уже есть.
  const editIds = [...(session.result?.edits ?? []).map((e) => e.edit_job_id), ...(session.result?.repairs ?? []).map((r) => r.repair_job_id)];
  const [knownEdits, setKnownEdits] = useState<string[]>(editIds);
  if (prevSource !== source) {
    setPrevSource(source);
    setTouched(false);
    setAppliedArtifact(null);
    setKnownEdits(editIds);
  }
  const templateId = template?.id;
  const latestJob = latest?.jobId;
  const latestArtifact = latest?.artifact;
  const docRevision = doc?.revision;
  // Сама открывается только новая версия того, что уже открыто: следующая ревизия того же
  // варианта или новое задание. Другой вариант той же сборки — выбор человека (переключатель,
  // закреплённый адрес), его не подменяем. Документ должен быть загружен и без правок.
  const opened = revisionOf(source.artifact);
  const next = latestArtifact ? revisionOf(latestArtifact) : null;
  const newer = Boolean(changed && latestJob && latestArtifact) && (latestJob !== source.jobId
    || Boolean(opened && next && opened.variant === next.variant && next.revision > opened.revision));
  // Переключение — прямо при рендере, как и сброс выше: иначе один кадр показывал бы баннер.
  if (newer && latestJob && latestArtifact && !templateId && !editing && !error && !touched && docRevision === 0) {
    setDoc(null); setPreview(null); setPreviewError(""); setIndex(0);
    setSource({ jobId: latestJob, artifact: latestArtifact });
  }
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
  // Ассистент в чате отвечает о содержимом именно этой копии и её сохранённой ревизии.
  useEffect(() => {
    if (!id || revision === undefined) return;
    setOpenDocument(projectId, { document_id: id, revision });
    return () => setOpenDocument(projectId, null);
  }, [projectId, id, revision]);
  // Карта объектов сохранённой ревизии: в превью — рамки для выбора, в живом редакторе —
  // подписи выделенного объекта для чата.
  useEffect(() => {
    if (!id || revision === undefined) return;
    let cancelled = false;
    void api.office.objects(id, revision).then((value) => {
      if (!cancelled) { setObjectMap({ ...value, documentId: id }); setObjectError(""); }
    }).catch((e: Error) => { if (!cancelled) setObjectError(e.message); });
    return () => { cancelled = true; };
  }, [id, revision, objectAttempt]);
  const objectMapRef = useRef(objectMap);
  useEffect(() => { objectMapRef.current = objectMap; }, [objectMap]);
  const liveSelection = useCallback((value: LiveSelection | null) => {
    const map = objectMapRef.current?.objects ?? [];
    onLiveSelection?.(value && {
      ...value,
      // Подпись — текст объекта из карты: сначала среди тех, что там же относительно группы.
      objects: value.objects.map((obj) => {
        const named = obj.name ? map.filter((o) => o.slide === value.slide && o.name === obj.name) : [];
        const found = named.find((o) => Boolean(o.group_path?.length) === Boolean(obj.inGroup)) ?? named[0];
        return { ...obj, label: found?.label };
      }),
    });
  }, [onLiveSelection]);
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

  const applySlide = useCallback(async (body: OfficeApplySlide) => {
    if (manual) {
      if (!manualEdit.current) throw new Error("Дождитесь загрузки редактора.");
      return manualEdit.current.applySlide(body);
    }
    if (!doc || pollError || doc.active_key || doc.error) throw new Error("Дождитесь сохранения презентации.");
    if (busy.current) throw new Error("Предыдущая ИИ-правка ещё выполняется.");
    busy.current = true;
    setEditing(true);
    try {
      const result = await api.office.applySlide(doc.id, doc.revision, body);
      setDoc(result.document);
      setPreviewError("");
      return result.message;
    } finally { busy.current = false; setEditing(false); }
  }, [manual, doc, pollError]);

  // Правка слайда из чата пересобирает его в новой ревизии варианта. Копию не правили — она
  // переключается на новую ревизию целиком (выше); правили — сюда переносится только этот
  // слайд: сохранение, перенос, открытие на том же слайде, ручные правки остальных на месте.
  const opening = opened;
  // Исправление находок (из чата — «исправь замечания») тоже новая ревизия: её слайды
  // переносятся так же; номера слайдов — по плану ревизии (исправление знает их id).
  const pendingEdit = templateId || !opening ? undefined : (session.result?.edits ?? []).find((e) =>
    !knownEdits.includes(e.edit_job_id) && e.result === "applied" && e.new_revision && e.variant_id === opening.variant && e.new_revision > opening.revision);
  const pendingRepair = templateId || !opening || pendingEdit ? undefined : (session.result?.repairs ?? []).find((r) =>
    !knownEdits.includes(r.repair_job_id) && r.result === "applied" && r.new_revision && r.variant_id === opening.variant && r.new_revision > opening.revision);
  const pending = pendingEdit
    ? { id: pendingEdit.edit_job_id, variant: pendingEdit.variant_id, revision: pendingEdit.new_revision, slides: [pendingEdit.slide_index + 1], ids: [] as string[] }
    : pendingRepair
      ? { id: pendingRepair.repair_job_id, variant: pendingRepair.variant_id, revision: pendingRepair.new_revision, slides: [] as number[], ids: pendingRepair.changed_slide_ids ?? [] }
      : undefined;
  const pendingId = pending?.id;
  const applying = useRef<string | null>(null);
  useEffect(() => {
    if (!pending || !pending.revision || applying.current || editing || !doc) return;
    if (!touched && doc.revision === 0) return;
    const edit = { ...pending, revision: pending.revision };
    applying.current = edit.id;
    void (async () => {
      const slides = edit.slides.length ? edit.slides : await slideNumbers(source.jobId, edit.variant, edit.revision, edit.ids).catch(() => [] as number[]);
      for (const slide of slides) {
        const body = { job_id: source.jobId, variant_id: edit.variant, artifact_revision: edit.revision, slide, source_slide: slide };
        // Редактор мог как раз перезагружаться после другой правки: несколько попыток.
        for (let attempt = 0; attempt < 6; attempt++) {
          try {
            await applySlide(body);
            break;
          } catch (e) {
            const message = e instanceof Error ? e.message : "";
            if (attempt < 5 && /Дождитесь|ещё выполняется/.test(message)) { await new Promise((r) => setTimeout(r, 3000)); continue; }
            notifications.show({ color: "red", title: `Слайд ${slide} не перенесён в открытую презентацию`, message: `${message} Новая версия доступна кнопкой в баннере.` });
            return;
          }
        }
      }
      setAppliedArtifact(`${edit.variant}/r${edit.revision}/deck.pptx`);
    })().finally(() => {
      applying.current = null;
      setKnownEdits((known) => known.includes(edit.id) ? known : [...known, edit.id]);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingId, touched, doc?.revision, editing]);

  // Правка из чата в сохранённом превью (редактор закрыт): тот же сервер, ревизия — открытая.
  const previewEdit = useCallback(async (call: (id: string, revision: number) => Promise<{ document: OfficeDocument; changed: boolean; message: string }>) => {
    if (!doc || pollError) throw new Error("Дождитесь проверки сохранённой презентации.");
    if (busy.current) throw new Error("Предыдущая ИИ-правка ещё выполняется.");
    if (doc.active_key || doc.error) throw new Error("Завершите ручное редактирование во всех вкладках и дождитесь сохранения.");
    if (visibleRevision !== doc.revision) throw new Error("Дождитесь актуального превью перед ИИ-правкой.");
    busy.current = true;
    setEditing(true);
    try {
      const base = doc.revision;
      const result = await call(doc.id, base);
      setDoc(result.document);
      setPreviewError("");
      return outcomeOf(result, base);
    } finally { busy.current = false; setEditing(false); }
  }, [doc, pollError, visibleRevision]);

  useImperativeHandle(editRef, () => ({ insertImage: async (image) => {
    // Картинка встаёт на текущий слайд живого редактора; в сохранённом превью слайда нет.
    if (!manual || !manualEdit.current) throw new Error("Откройте редактор слайдов, чтобы вставить картинку.");
    return manualEdit.current.insertImage(image);
  }, applySlide, edit: async (instruction, target, logo, image) => {
    const live = target && "name" in target ? target : undefined;
    const picked = target && "documentId" in target ? target : undefined;
    if (manual) {
      if (picked && !logo) throw new Error("Для правки выбранных объектов откройте сохранённое превью и выберите их заново.");
      if (!manualEdit.current) throw new Error("Дождитесь загрузки редактора.");
      return manualEdit.current.edit(instruction, live, logo, image);
    }
    if (live) throw new Error("Редактор слайдов закрыт: выделите объект заново.");
    if (!doc || pollError) throw new Error("Дождитесь проверки сохранённой презентации.");
    if (busy.current) throw new Error("Предыдущая ИИ-правка ещё выполняется.");
    if (doc.active_key || doc.error) throw new Error("Завершите ручное редактирование во всех вкладках и дождитесь сохранения.");
    if (visibleRevision !== doc.revision) throw new Error("Дождитесь актуального превью перед ИИ-правкой.");
    if (!logo && picked && (picked.documentId !== doc.id || picked.revision !== doc.revision || picked.slide !== index + 1)) throw new Error("Выбранный объект устарел. Выберите его заново.");
    busy.current = true;
    setEditing(true);
    try {
      const targets = logo || image ? undefined : picked?.objects.map(({ slide, shape_id }) => ({ slide, shape_id }));
      const result = await api.office.edit(doc.id, doc.revision, instruction, targets?.length === 1 ? targets[0] : targets, logo, image);
      setDoc(result.document);
      setPreviewError("");
      return result.changed ? `Правка сохранена в этом PPTX · v${result.document.revision}. ${result.message}` : `Документ не изменён. ${result.message}`;
    } finally { busy.current = false; setEditing(false); }
  }, run: async (request) => {
    if (manual) {
      if (!manualEdit.current) throw new Error("Дождитесь загрузки редактора.");
      return manualEdit.current.run(request);
    }
    return previewEdit((id, revision) => api.office.edit(id, revision, request.instruction, request.live, request.logo, request.image, request.slides, request.table, request.chart));
  }, undo: async (revision, to) => {
    if (manual) {
      if (!manualEdit.current) throw new Error("Дождитесь загрузки редактора.");
      return manualEdit.current.undo(revision, to);
    }
    const outcome = await previewEdit((id) => api.office.undo(id, revision, to));
    return { ...outcome, base: revision };
  } }), [manual, doc, pollError, visibleRevision, index, applySlide, previewEdit]);

  const selectable = !manual && !editing && !pollError && !doc?.active_key && !doc?.error && visiblePreview?.revision === doc?.revision && objectMap?.documentId === doc?.id && objectMap?.revision === doc?.revision;
  const currentObjects = selectable ? objectMap?.objects.filter((obj) => obj.slide === index + 1) ?? [] : [];

  const retry = () => { setError(""); setPreviewError(""); setAttempt((n) => n + 1); };

  // Варианты собираются по очереди: первый открыт, пока остальные доделываются в фоне. Все три
  // видны сразу, неготовые — с загрузкой и без нажатия. Выбор открывает PPTX этого варианта;
  // правки прежнего остаются в его собственной офисной копии.
  const variants = templateId ? [] : session.result?.variants ?? [];
  const switchVariant = (value: string) => {
    const v = variants.find((item) => item.variant_id === value);
    if (!v?.artifacts?.pptx || !session.jobId || editing) return;
    session.setSelectedVariant(value);
    if (source.jobId === session.jobId && source.artifact === v.artifacts.pptx) return;
    const next = { jobId: session.jobId, artifact: v.artifacts.pptx };
    setDoc(null); setPreview(null); setError(""); setPreviewError(""); setIndex(0); setSource(next);
    router.replace(`/project?${new URLSearchParams({ id: projectId, officeJob: next.jobId, officeArtifact: next.artifact })}`);
  };
  // Отмечен вариант, чей PPTX открыт сейчас, а не выбранный в сессии: до переключения это одно.
  const shownVariant = source.jobId === session.jobId ? variants.find((v) => v.artifacts?.pptx === source.artifact) : undefined;
  const switcher = variants.length > 1 && (
    <SegmentedControl size="xs" value={shownVariant?.variant_id ?? ""} onChange={switchVariant} data-testid="office-variants" aria-label="Вариант презентации"
      data={variants.map((v) => {
        const ready = Boolean(v.artifacts?.pptx);
        const failed = v.status === "failed";
        const label = VARIANT_LABELS[v.variant_id] ?? v.variant_id;
        return {
          value: v.variant_id,
          disabled: !ready || editing,
          label: <Group gap={6} wrap="nowrap" title={ready ? label : failed ? `${label}: не собрался` : `${label}: собирается`} data-testid={`office-variant-${v.variant_id}`} data-state={ready ? "ready" : failed ? "failed" : "building"}>
            {!ready && !failed && <Loader size={10} aria-label="собирается" />}
            <span style={failed ? { textDecoration: "line-through" } : undefined}>{label}</span>
          </Group>,
        };
      })} />
  );
  const actions = doc && <Group gap="xs">
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
    {switcher && (actionsTarget ? createPortal(switcher, actionsTarget) : <Group p="xs">{switcher}</Group>)}
    {!manual && (actionsTarget ? createPortal(actions, actionsTarget) : actions)}
    {templateId && <Text size="xs" c="dimmed" p="xs">Рабочая копия шаблона · ИИ-правки сохраняются только в этом проекте. Исходный шаблон не изменяется.</Text>}
    {!session.terminal && variants.length > 1 && variants.some((v) => !v.artifacts?.pptx && v.status !== "failed") && <Text size="xs" c="dimmed" p="xs" role="status">Остальные варианты ещё собираются — их можно будет открыть переключателем вверху.</Text>}
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
    {manual ? (doc ? <OfficeEditor key={doc.id} id={doc.id} title={title} embedded editRef={manualEdit} actionsTarget={actionsTarget} onSaved={saved} onReady={onEditorReady} onModifiedChange={onModified} onSelection={liveSelection} />
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