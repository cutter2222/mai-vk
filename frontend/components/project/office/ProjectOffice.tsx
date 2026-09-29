"use client";

import { Alert, Button, Group, Text } from "@mantine/core";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { createPortal } from "react-dom";
import { notifications } from "@mantine/notifications";

import { OfficeEditor, type OfficeEditHandle } from "@/components/office/OfficeEditor";
import { OfficeLoading } from "@/components/office/OfficeLoading";
import { VARIANT_ORDER, VariantPicker } from "./VariantPicker";
import { api, type OfficeApplySlide, type OfficeDocument, type OfficeObject, type TemplateDetail } from "@/lib/api/client";
import type { LiveSelection } from "@/lib/editor/officeLive";
import type { GenerationSession } from "@/lib/hooks/useGenerationSession";
import { setOpenDocument } from "@/lib/state/projects";
import { slideNumbers } from "../chat/routeRunner";

/** «balanced/r2/deck.pptx» → вариант и номер ревизии артефакта. */
function revisionOf(artifact: string): { variant: string; revision: number } | null {
  const match = /^([^/]+)\/r(\d+)\//.exec(artifact);
  return match ? { variant: match[1], revision: Number(match[2]) } : null;
}

/**
 * Презентация в проекте — всегда открытый редактор ONLYOFFICE над серверной копией PPTX.
 * Отдельного «сохранённого превью» с выбором объектов по картинке больше нет (28.09.2026):
 * выделение читается из живого редактора, ИИ-правки ложатся в открытую копию, автосохранение
 * включено, а скачивание берёт последнюю серверную ревизию. Кнопки «На весь экран» тоже нет
 * (29.09): больше места даёт сворачивание чата без второй сессии редактора; страница `/office`
 * остаётся доступной по адресу.
 */
export function ProjectOffice({ session, title, projectId, editRef, actionsTarget, template, onEditorReady, onLiveSelection, onAddVariant }: {
  /** Собрать тип вёрстки, которого в задании нет. */
  onAddVariant?: (variantId: string) => void;
  session: GenerationSession; title: string; projectId: string;
  /** Текущий слайд и выделение живого редактора — адресат сообщения в чате. */
  onLiveSelection?: (value: LiveSelection | null) => void;
  /** Редактор открыл документ: слайды видны. */
  onEditorReady?: () => void;
  editRef?: Ref<OfficeEditHandle>; actionsTarget?: HTMLElement | null;
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
  const manualEdit = useRef<OfficeEditHandle>(null);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [objectMap, setObjectMap] = useState<{ documentId: string; revision: number; objects: OfficeObject[] } | null>(null);
  const transferErrors = useRef(new Map<string, Error>());
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
  if (newer && latestJob && latestArtifact && !templateId && !error && !touched && docRevision === 0) {
    setDoc(null);
    setSource({ jobId: latestJob, artifact: latestArtifact });
  }

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
  // Ассистент в чате отвечает о содержимом именно этой копии и её сохранённой ревизии.
  useEffect(() => {
    if (!id || revision === undefined) return;
    setOpenDocument(projectId, { document_id: id, revision });
    return () => setOpenDocument(projectId, null);
  }, [projectId, id, revision]);
  // Карта объектов сохранённой ревизии: подписи выделенного в редакторе объекта для чата.
  // Без неё выделение всё равно адресуется, только без подписи, поэтому ошибка не показывается.
  useEffect(() => {
    if (!id || revision === undefined) return;
    let cancelled = false;
    void api.office.objects(id, revision).then((value) => {
      if (!cancelled) setObjectMap({ ...value, documentId: id });
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [id, revision]);
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

  const applySlide = useCallback(async (body: OfficeApplySlide) => {
    if (!manualEdit.current) throw new Error("Дождитесь загрузки редактора.");
    return manualEdit.current.applySlide(body);
  }, []);

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
    if (!pending || !pending.revision || applying.current || !doc) return;
    if (!touched && doc.revision === 0) return;
    const edit = { ...pending, revision: pending.revision };
    applying.current = edit.id;
    void (async () => {
      const slides = edit.slides.length ? edit.slides : await slideNumbers(source.jobId, edit.variant, edit.revision, edit.ids);
      if (!slides.length) throw new Error("Не удалось определить слайды для переноса.");
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
            throw e;
          }
        }
      }
      setAppliedArtifact(`${edit.variant}/r${edit.revision}/deck.pptx`);
    })().catch((e: unknown) => {
      const error = e instanceof Error ? e : new Error("Перенос не подтверждён.");
      transferErrors.current.set(edit.id, error);
      notifications.show({ color: "red", title: "Правка не перенесена в открытую презентацию", message: `${error.message} Новая версия доступна кнопкой в баннере.` });
    }).finally(() => {
      applying.current = null;
      setKnownEdits((known) => known.includes(edit.id) ? known : [...known, edit.id]);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingId, touched, doc?.revision]);

  // Ожидание читает актуальный render, а не замыкание до завершения backend-задания.
  const transferState = useRef<() => { jobId: string; variant?: string; revision: number; known: string[]; ready: boolean }>(() => ({ jobId: "", revision: 0, known: [], ready: false }));
  useEffect(() => {
    transferState.current = () => ({
      jobId: source.jobId, variant: opened?.variant, revision: opened?.revision ?? 0,
      known: knownEdits,
      ready: Boolean(doc && !error && !pollError && !doc.error && manualEdit.current?.isReady?.()),
    });
  });
  const waitForApplied = useCallback(async (jobId: string, variantId: string, revision: number, editJobId: string) => {
    const until = Date.now() + 120000;
    while (Date.now() < until) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      const state = transferState.current();
      if (state.jobId !== jobId || state.variant !== variantId) throw new Error("Откройте вариант, в котором была сделана правка.");
      const error = transferErrors.current.get(editJobId);
      if (error) throw error;
      if (state.ready && (state.revision >= revision || state.known.includes(editJobId))) return;
    }
    throw new Error("Перенос правки в открытую презентацию не подтверждён. Следующие шаги не выполняю.");
  }, []);

  const editor = () => {
    if (!manualEdit.current) throw new Error("Дождитесь загрузки редактора.");
    return manualEdit.current;
  };
  useImperativeHandle(editRef, () => ({
    insertImage: async (image) => editor().insertImage(image),
    applySlide,
    waitForApplied,
    edit: async (instruction, target, logo, image) => {
      const live = target && "name" in target ? target : undefined;
      return editor().edit(instruction, live, logo, image);
    },
    run: async (request) => editor().run(request),
    undo: async (revision, to, documentId) => {
      if (documentId !== id) throw new Error("Откройте презентацию, в которой была сделана эта правка.");
      return editor().undo(revision, to, documentId);
    },
  }), [id, applySlide, waitForApplied]);

  const retry = () => { setError(""); setAttempt((n) => n + 1); };
  const open = (next: { jobId: string; artifact: string }) => {
    setDoc(null); setError(""); setSource(next);
    router.replace(`/project?${new URLSearchParams({ id: projectId, officeJob: next.jobId, officeArtifact: next.artifact })}`);
  };

  // Варианты собираются по очереди: первый открыт, пока остальные доделываются в фоне. Все три
  // видны сразу, неготовые — с загрузкой и без нажатия. Выбор открывает PPTX этого варианта;
  // правки прежнего остаются в его собственной офисной копии.
  const variants = templateId ? [] : session.result?.variants ?? [];
  const switchVariant = (value: string) => {
    const v = variants.find((item) => item.variant_id === value);
    if (!v?.artifacts?.pptx || !session.jobId) return;
    session.setSelectedVariant(value);
    if (source.jobId === session.jobId && source.artifact === v.artifacts.pptx) return;
    open({ jobId: session.jobId, artifact: v.artifacts.pptx });
  };
  // Отмечен вариант, чей PPTX открыт сейчас, а не выбранный в сессии: до переключения это одно.
  const shownVariant = source.jobId === session.jobId ? variants.find((v) => v.artifacts?.pptx === source.artifact) : undefined;
  // Список типов вёрстки есть всегда, когда презентация собрана (29.09.2026): задание из одного
  // варианта показывало только его, и выбрать другой тип было негде. Несобранный тип можно
  // собрать; готовая презентация (вариант «original») типов вёрстки не имеет.
  const byId = new Map(variants.map((v) => [v.variant_id, v]));
  const switcher = variants.length > 0 && !byId.has("original") && (
    <VariantPicker
      value={shownVariant?.variant_id ?? ""}
      onChange={switchVariant}
      onAdd={onAddVariant}
      options={VARIANT_ORDER.map((id) => {
        const v = byId.get(id);
        return v
          ? { id, ready: Boolean(v.artifacts?.pptx), failed: v.status === "failed" }
          : { id, ready: false, failed: false, absent: true };
      })}
    />
  );

  return <div className="project-office" data-testid="project-office">
    {switcher && (actionsTarget ? createPortal(switcher, actionsTarget) : <Group p="xs">{switcher}</Group>)}
    {templateId && <Text size="xs" c="dimmed" p="xs">Рабочая копия шаблона · ИИ-правки сохраняются только в этом проекте. Исходный шаблон не изменяется.</Text>}
    {!session.terminal && variants.length > 1 && variants.some((v) => !v.artifacts?.pptx && v.status !== "failed") && <Text size="xs" c="dimmed" p="xs" role="status">Остальные варианты ещё собираются — их можно будет выбрать в списке вариантов вверху.</Text>}
    {changed && <Alert color="blue" title="Доступна другая версия презентации">
      Ручные и ИИ-правки остаются в текущем PPTX и не переносятся в другую сборку.
      <Button ml="sm" size="xs" disabled={!doc || Boolean(doc.active_key) || Boolean(doc.error) || Boolean(pollError)} onClick={() => { if (latest) open(latest); }}>Открыть выбранную версию</Button>
    </Alert>}
    {pollError && <Alert color="yellow">Сохранение не подтверждено: {pollError}</Alert>}
    {doc?.error && <Alert color="red">{doc.error}</Alert>}
    {error && <Alert color="red">{error}<Button ml="sm" size="xs" onClick={retry}>Повторить открытие</Button></Alert>}
    {doc
      ? <OfficeEditor key={doc.id} id={doc.id} title={title} embedded editRef={manualEdit} actionsTarget={actionsTarget} onReady={onEditorReady} onModifiedChange={onModified} onSelection={liveSelection} />
      : !error && <div className="office-canvas"><OfficeLoading stage="copy" /></div>}
  </div>;
}
