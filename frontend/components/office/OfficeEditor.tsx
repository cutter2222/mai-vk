"use client";

import { ActionIcon, Alert, Button, Group, Loader, Menu, Select, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDots, IconDownload, IconPhotoPlus } from "@tabler/icons-react";
import Link from "next/link";
import { useCallback, useEffect, useId, useImperativeHandle, useRef, useState, type ReactNode, type Ref } from "react";
import { createPortal } from "react-dom";

import { api, type OfficeApplySlide, type OfficeChartPlacement, type OfficeDocument, type OfficeImagePlacement, type OfficeLiveTarget, type OfficeLogoAction } from "@/lib/api/client";
import { downloadArtifact } from "@/lib/download";
import { Logo } from "@/components/app/Logo";
import { goToOfficeSlide, watchOfficeSelection, type LiveSelection } from "@/lib/editor/officeLive";
import { flushOfficeFrame } from "@/lib/editor/officeSave";
import { draggedImage, setDraggedImage, useDraggedImage, type DraggedImage } from "@/lib/state/drag";

type Editor = { destroyEditor: () => void; requestClose: () => void; insertImage?: (command: Record<string, unknown>) => void };
export type OfficeEditHandle = {
  isReady?: () => boolean;
  /** ProjectOffice подтверждает перенос результата, а не только завершение backend job. */
  waitForApplied?: (jobId: string, variantId: string, revision: number, editJobId: string) => Promise<void>;
  edit: (instruction: string, target?: import("@/lib/api/client").OfficeSelection | OfficeLiveTarget, logo?: import("@/lib/api/client").OfficeLogoAction, image?: import("@/lib/api/client").OfficeImagePlacement) => Promise<string>;
  /** Слайд, пересобранный правкой из чата, — в открытую копию: остальные слайды не меняются. */
  applySlide: (body: OfficeApplySlide) => Promise<string>;
  /** Картинка из файлов проекта или шаблона — на текущий слайд открытого редактора. */
  insertImage: (image: DraggedImage) => Promise<void>;
  /** Правка копии на сервере с итогом для карточки: ревизии до и после. */
  run: (request: OfficeEditRequest) => Promise<OfficeEditOutcome>;
  /** Отмена правки: `revision` — ревизия, которую она дала, `to` — ревизия до неё. */
  undo: (revision: number, to: number, documentId: string) => Promise<OfficeEditOutcome>;
};

/** Правка копии из чата: объект по имени, логотип, картинка или текст на слайдах. */
export type OfficeEditRequest = {
  instruction: string;
  live?: OfficeLiveTarget;
  logo?: OfficeLogoAction;
  image?: OfficeImagePlacement;
  slides?: number[];
  /** Таблица из приложенного xlsx или csv: файл проекта и слайд. */
  table?: OfficeImagePlacement;
  /** Диаграмма: из файла, по картинке графика или «сделай редактируемой». */
  chart?: OfficeChartPlacement;
};

/** Итог правки копии: для ленты и для «Отменить». */
export type OfficeEditOutcome = { changed: boolean; message: string; documentId: string; base: number; revision: number };

export function outcomeOf(result: { document: OfficeDocument; changed: boolean; message: string }, base: number): OfficeEditOutcome {
  return { changed: result.changed, message: result.message, documentId: result.document.id, base, revision: result.document.revision };
}
let sdkPromise: Promise<void> | undefined;

export function loadSDK(url: string): Promise<void> {
  if (!sdkPromise) {
    sdkPromise = new Promise<void>((resolve, reject) => {
      const script = document.createElement("script");
      script.src = url;
      script.onload = () => resolve();
      script.onerror = () => {
        script.remove();
        sdkPromise = undefined;
        reject(new Error("Не удалось загрузить ONLYOFFICE. Проверьте запуск сервиса."));
      };
      // DocsAPI discovers its base URL from this element on every editor creation.
      // Keep it after closing the editor, including across client-side navigation.
      document.head.appendChild(script);
    });
  }
  return sdkPromise;
}

let warmed = false;
/**
 * Прогрев редактора, пока презентация ещё готовится: SDK и штатный `DocEditor.warmUp`
 * ONLYOFFICE (невидимый iframe с preload.html) кладут скрипты редактора в кэш браузера,
 * и документ открывается без их загрузки. Ошибка прогрева ничего не ломает.
 */
export function warmUpOffice(url: string): void {
  if (warmed) return;
  warmed = true;
  void loadSDK(url).then(() => {
    const warmUp = window.DocsAPI?.DocEditor.warmUp;
    if (!warmUp) return;
    const holder = document.createElement("div");
    holder.id = "office-warmup";
    holder.hidden = true;
    document.body.appendChild(holder);
    warmUp(holder.id);
  }).catch(() => { warmed = false; });
}

declare global {
  interface Window {
    DocsAPI?: { DocEditor: { new (id: string, config: Record<string, unknown>): Editor; warmUp?: (id: string) => void } };
  }
}

export function OfficeEditor({ id, title, embedded = false, onActiveChange, documentActions, editRef, actionsTarget, returnHref = "/", onSaved, onReady, onModifiedChange, onSelection }: {
  id: string;
  title?: string;
  embedded?: boolean;
  onActiveChange?: (active: boolean) => void;
  documentActions?: ReactNode;
  editRef?: Ref<OfficeEditHandle>;
  actionsTarget?: HTMLElement | null;
  returnHref?: string;
  onSaved?: (document: OfficeDocument) => void;
  /** Документ открыт и слайды видны. */
  onReady?: () => void;
  /** Редактор сообщил о несохранённых правках (или что их больше нет). */
  onModifiedChange?: (modified: boolean) => void;
  /** Текущий слайд и выделение живого редактора; `null` — редактор закрыт или моста нет. */
  onSelection?: (value: LiveSelection | null) => void;
}) {
  const [doc, setDoc] = useState<OfficeDocument | null>(null);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [closed, setClosed] = useState(false);
  const [version, setVersion] = useState<string | null>(null);
  const [modified, setModified] = useState(false);
  const [pollError, setPollError] = useState("");
  const [startFailed, setStartFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const editorId = useId();
  const [editing, setEditing] = useState(false);
  const [closing, setClosing] = useState(false);
  const busy = useRef(false);
  const mounted = useRef(true);
  // Готовность — и ссылкой: правка из чата сразу после предыдущей ждёт, пока редактор
  // откроется заново, а не отказывает («Отменить» нажимают, пока он перезагружается).
  const editorReady = useRef(false);
  useEffect(() => { editorReady.current = ready; }, [ready]);
  const sdk = useRef<Editor | null>(null);
  const canvas = useRef<HTMLDivElement | null>(null);
  const saveAbort = useRef<AbortController | null>(null);
  const dirty = useRef(false);
  const closeRequested = useRef<(() => void) | null>(null);
  const pollEpoch = useRef(0);
  const readyRef = useRef(onReady);
  useEffect(() => { readyRef.current = onReady; }, [onReady]);
  const modifiedRef = useRef(onModifiedChange);
  useEffect(() => { modifiedRef.current = onModifiedChange; }, [onModifiedChange]);
  const selectionRef = useRef(onSelection);
  useEffect(() => { selectionRef.current = onSelection; }, [onSelection]);
  // Слайд, на котором человек был: после правки на сервере редактор открывается на нём же.
  const lastSlide = useRef(0);
  const unwatch = useRef<(() => void) | null>(null);
  const returnedAfterSave = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; saveAbort.current?.abort(); }; }, []);

  const flush = async () => {
    saveAbort.current?.abort();
    const controller = new AbortController();
    saveAbort.current = controller;
    await flushOfficeFrame(canvas.current?.querySelector("iframe") ?? null, controller.signal);
    dirty.current = false;
    setModified(false);
  };

  useEffect(() => {
    // finish() clears pre-close state; only a fresh server acknowledgement allows return.
    if (editing || !closed || !doc || doc.active_key || doc.error || pollError || !onSaved || returnedAfterSave.current) return;
    returnedAfterSave.current = true;
    onSaved(doc);
  }, [embedded, editing, closed, doc, pollError, onSaved]);

  const finish = async () => {
    if (busy.current || closing || closed || !sdk.current) return;
    busy.current = true;
    setClosing(true);
    setError("");
    try {
      await flush();
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => {
          closeRequested.current = null;
          reject(new Error("Редактор не подтвердил закрытие. Повторите завершение."));
        }, 15000);
        closeRequested.current = () => {
          clearTimeout(timer);
          closeRequested.current = null;
          if (dirty.current) reject(new Error("Есть несохранённые изменения; редактор оставлен открытым."));
          else {
            // Discard pre-close polling state: only a fresh callback acknowledgement permits return.
            pollEpoch.current++;
            setDoc(null);
            setClosed(true);
            resolve();
          }
        };
        sdk.current!.requestClose();
      });
    } catch (e) { if (mounted.current) setError(e instanceof Error ? e.message : "Не удалось завершить редактирование"); }
    finally { busy.current = false; if (mounted.current) setClosing(false); }
  };

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!closed || !doc || doc.active_key || doc.error || pollError) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [closed, doc, pollError]);

  // Вставка картинки: сервер подписывает ссылку на байты, Document Server сам скачивает их
  // и ставит картинку на текущий слайд; сохранение идёт обычным путём редактора.
  const insertImage = useCallback(async (image: DraggedImage) => {
    if (busy.current || editing || closing) throw new Error("Дождитесь окончания ИИ-правки.");
    if (!ready || closed || !sdk.current) throw new Error("Дождитесь открытия редактора слайдов.");
    if (!sdk.current.insertImage) throw new Error("Редактор не поддерживает вставку картинок.");
    const command = await api.office.imageCommand(id, image);
    if (!mounted.current || !sdk.current?.insertImage) return;
    sdk.current.insertImage({ ...command });
  }, [id, ready, closed, editing, closing]);
  const dragged = useDraggedImage();
  const [dropOver, setDropOver] = useState(false);
  const dropImage = async (image: DraggedImage) => {
    try {
      await insertImage(image);
    } catch (e) {
      notifications.show({ color: "red", title: "Картинка не вставлена", message: e instanceof Error ? e.message : "Повторите перетаскивание." });
    }
  };

  useImperativeHandle(editRef, () => {
    // Правка на сервере идёт по сохранённой копии: несохранённое сбрасывается в файл, редактор
    // закрывается, сервер пишет следующую ревизию, редактор открывается на ней на том же слайде.
    const serverEdit = async (run: (revision: number) => Promise<{ document: OfficeDocument; changed: boolean; message: string }>) => {
      if (busy.current) throw new Error("Предыдущая ИИ-правка ещё выполняется.");
      for (let waited = 0; !editorReady.current && !closed && waited < 60000 && mounted.current; waited += 500) {
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      if (!(editorReady.current || closed) || error || pollError || doc?.error) throw new Error("Сначала дождитесь готовности редактора и устраните ошибку сохранения.");
      busy.current = true;
      setEditing(true);
      let saved = false;
      try {
        if (!closed) {
          await flush();
          await new Promise<void>((resolve, reject) => {
            const timer = setTimeout(() => { closeRequested.current = null; reject(new Error("Редактор не подтвердил закрытие. Сохраните документ и повторите запрос.")); }, 15000);
            closeRequested.current = () => {
              clearTimeout(timer);
              closeRequested.current = null;
              if (dirty.current) reject(new Error("Есть несохранённые изменения; редактор оставлен открытым."));
              else { setClosed(true); resolve(); }
            };
            if (!sdk.current) { clearTimeout(timer); closeRequested.current = null; reject(new Error("Редактор недоступен.")); }
            else sdk.current.requestClose();
          });
        }
        // Closing the SDK starts final save. A clean client flag is NOT a storage ack.
        const until = Date.now() + 90000;
        while (Date.now() < until) {
          await new Promise((resolve) => setTimeout(resolve, 1000));
          if (!mounted.current) throw new Error("Страница закрыта; ИИ-правка не запущена.");
          const current = await api.office.get(id);
          if (current.error) throw new Error(current.error);
          if (current.active_key) continue;
          saved = true;
          const result = await run(current.revision);
          if (mounted.current) { setDoc(result.document); setVersion(null); }
          return result;
        }
        throw new Error("Сохранение не подтверждено. Закройте другие вкладки этого документа и повторите запрос. PPTX не перезаписан.");
      } finally {
        busy.current = false;
        if (mounted.current) {
          setEditing(false);
          if (saved) { editorReady.current = false; setReady(false); setModified(false); setError(""); setClosed(false); }
        }
      }
    };
    return {
      isReady: () => mounted.current && editorReady.current && !busy.current,
      insertImage,
      edit: async (instruction: string, target?: unknown, logo?: OfficeLogoAction, image?: OfficeImagePlacement) => {
        const live = target && typeof target === "object" && "name" in target ? target as OfficeLiveTarget : undefined;
        const result = await serverEdit((revision) => api.office.edit(id, revision, instruction, live, logo, image));
        return result.changed ? `Правка сохранена в этом PPTX · v${result.document.revision}. ${result.message}` : `Документ не изменён. ${result.message}`;
      },
      applySlide: async (body: OfficeApplySlide) => (await serverEdit((revision) => api.office.applySlide(id, revision, body))).message,
      run: async (request: OfficeEditRequest) => {
        let base = 0;
        const result = await serverEdit((revision) => {
          base = revision;
          return api.office.edit(id, revision, request.instruction, request.live, request.logo, request.image, request.slides, request.table, request.chart);
        });
        return outcomeOf(result, base);
      },
      undo: async (revision: number, to: number, documentId: string) => {
        if (documentId !== id) throw new Error("Откройте презентацию, в которой была сделана эта правка.");
        // Сервер сверяет содержимое с результатом правки; чужие изменения отменять нельзя.
        const result = await serverEdit(() => api.office.undo(id, revision, to));
        return outcomeOf(result, revision);
      },
    };
  }, [id, error, pollError, doc?.error, closed, insertImage]);

  useEffect(() => {
    onActiveChange?.(editing || !closed || !doc || Boolean(doc.active_key) || Boolean(pollError));
  }, [closed, doc, pollError, onActiveChange, editing]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const epoch = pollEpoch.current;
      try {
        const value = await api.office.get(id);
        if (!cancelled && epoch === pollEpoch.current) {
          setDoc(value);
          setPollError("");
        }
      } catch (e) {
        if (!cancelled) setPollError(e instanceof Error ? e.message : "Сервер недоступен");
      } finally {
        if (!cancelled) timer = setTimeout(poll, 2000);
      }
    };
    void poll();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [id, closed]);

  useEffect(() => {
    if (!id || closed) return;
    let cancelled = false;
    let editor: Editor | undefined;
    const start = async () => {
      const response = await api.office.config(id);
      if (cancelled) return;
      await loadSDK(response.script_url);
      if (cancelled) return;
      if (!window.DocsAPI) throw new Error("ONLYOFFICE SDK не загрузился. Попробуйте открыть редактор снова.");
      editor = new window.DocsAPI.DocEditor(editorId, {
        ...response.config,
        events: {
          onDocumentReady: () => {
            if (cancelled) return;
            setReady(true);
            readyRef.current?.();
            const frame = canvas.current?.querySelector("iframe") ?? null;
            if (lastSlide.current > 1) setTimeout(() => goToOfficeSlide(frame, lastSlide.current), 0);
            unwatch.current?.();
            unwatch.current = watchOfficeSelection(frame, (value) => {
              lastSlide.current = value.slide;
              selectionRef.current?.(value);
            });
          },
          onDocumentStateChange: (event: { data: boolean }) => { if (!cancelled) { dirty.current = event.data; setModified(event.data); modifiedRef.current?.(event.data); } },
          onRequestClose: () => { if (!cancelled) closeRequested.current?.(); },
          onError: () => { if (!cancelled) setError("Ошибка ONLYOFFICE. Не закрывайте вкладку до подтверждения сохранения на сервере."); },
        },
      });
      sdk.current = editor;
    };
    void start().catch((e: Error) => { if (!cancelled) { setError(e.message); setStartFailed(true); } });
    return () => {
      cancelled = true;
      unwatch.current?.();
      unwatch.current = null;
      selectionRef.current?.(null);
      if (sdk.current === editor) sdk.current = null;
      editor?.destroyEditor();
    };
  }, [id, closed, editorId, attempt]);

  const [downloading, setDownloading] = useState(false);
  const download = async (format: "pptx" | "pdf" | "html") => {
    if (!id || !doc) return;
    const revision = version === null ? doc.revision : Number(version);
    setDownloading(true);
    try {
      await downloadArtifact(api.office.downloadUrl(id, revision, format), `${title || `office-${id}`}-v${revision}.${format}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Файл не скачан");
    } finally {
      setDownloading(false);
    }
  };

  if (!id) return <Alert color="red">Офисная копия не указана. Откройте её из проекта.</Alert>;
  const actions = (
        <Group gap="xs" wrap="nowrap">
          {embedded && onSaved && <Button size="xs" variant="light" loading={closing} disabled={closed || !ready || editing} onClick={() => void finish()} data-testid="office-preview">Сохранить и превью</Button>}
          <Menu withinPortal position="bottom-end" width={180} shadow="md">
            <Menu.Target>
              <ActionIcon variant="subtle" color="gray" aria-label="Скачать презентацию" title="Скачать презентацию" loading={downloading} disabled={!doc || Boolean(pollError)} data-testid="download-menu">
                <IconDownload size={18} />
              </ActionIcon>
            </Menu.Target>
            <Menu.Dropdown>
              {(["pptx", "pdf", "html"] as const).map((format) => (
                <Menu.Item key={format} onClick={() => void download(format)} data-testid={`dl-${format}`}>{format.toUpperCase()}</Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
          <Menu withinPortal position="bottom-end" width={300} closeOnItemClick={false}>
            <Menu.Target>
              <ActionIcon variant="subtle" color="gray" aria-label="Действия с презентацией"><IconDots size={18} /></ActionIcon>
            </Menu.Target>
            <Menu.Dropdown>
              <Text size="xs" c="dimmed" px="sm" pb="xs" role="status">
                {pollError ? "Сохранение не подтверждено" : modified ? "Есть несохранённые правки" : doc ? `На сервере · v${doc.revision}` : "Проверяем сохранение…"}
              </Text>
              <Menu.Label>Сохранённые версии</Menu.Label>
              <Select mx="xs" mb="xs" size="xs" aria-label="Сохранённая версия для скачивания" value={version} onChange={setVersion} clearable
                comboboxProps={{ withinPortal: false }} placeholder={`Последняя: v${doc?.revision ?? 0}`}
                data={(doc?.revisions ?? []).map((r) => ({ value: String(r.revision), label: `v${r.revision} · ${r.revision === 0 ? "начальная версия" : "сохранено на сервере"}` }))} />
              <Text size="xs" c="dimmed" px="sm" pb="xs">Ctrl+S / ⌘S — сохранить. Скачивается серверная версия; последние правки могут ещё сохраняться.</Text>
              {documentActions}
              <Menu.Divider />
              <Menu.Item disabled={closed || editing || closing || !ready} onClick={() => void finish()}>Завершить редактирование</Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </Group>
  );

  return (
    <Stack gap={0} className="office-workspace" data-testid="office-workspace" style={{ height: embedded ? "100%" : "100dvh" }}>
      {!embedded && <Group component="header" justify="space-between" wrap="nowrap" px="md" h={60} style={{ flexShrink: 0, background: "var(--page)", borderBottom: "1px solid var(--line)" }}>
        <span role="img" aria-label="Дизайнер презентаций" style={{ display: "inline-flex" }}><Logo /></span>
        {!closed && <Button loading={closing} disabled={!ready || editing} onClick={() => void finish()}>Завершить и сохранить</Button>}
      </Group>}
      {actionsTarget && createPortal(actions, actionsTarget)}
      {pollError && <Alert color="yellow">Не удаётся проверить сохранение: {pollError}</Alert>}
      {(error || doc?.error) && <Alert color="red">
        {error || doc?.error}
        {startFailed && !closed && <Button ml="sm" size="xs" variant="light" onClick={() => { setError(""); setStartFailed(false); setAttempt((n) => n + 1); }}>Повторить загрузку редактора</Button>}
      </Alert>}
      {editing && <Alert color="blue" title="ИИ редактирует этот PPTX">Ожидаем сохранения ONLYOFFICE и применяем точечную правку. Редактор откроется автоматически. Не закрывайте страницу.</Alert>}
      {closed ? (editing ? null : (
        <Alert color={!doc || doc.active_key || doc.error || pollError ? "yellow" : "green"}>
          {!doc || doc.active_key || doc.error || pollError ? "Ожидаем завершения сессии и сохранения. Если файл открыт в другой вкладке, завершите редактирование и там." : onSaved && !embedded ? "Возвращаемся в ИИ-редактор…" : `Сессия закрыта. Версия v${doc.revision} сохранена на сервере.`}
          {(!onSaved || embedded) && doc && !doc.active_key && !doc.error && !pollError && <Button ml="md" variant="light" onClick={() => { setReady(false); setModified(false); setError(""); setClosed(false); }}>Открыть снова</Button>}
          {!embedded && !onSaved && doc && !doc.active_key && !doc.error && !pollError && <Button component={Link} href={returnHref} ml="md">Вернуться к превью</Button>}
        </Alert>
      )) : (
        <>
          <div ref={canvas} className="office-canvas" inert={editing || closing} aria-label="Редактор презентации ONLYOFFICE">
            {!ready && !error && <div className="office-loading"><Loader size="sm" /><Text size="sm">Загружается редактор…</Text></div>}
            <div id={editorId} />
            {/* Крышка над iframe на время перетаскивания: сам iframe событий родителю не отдаёт. */}
            {dragged && ready && (
              <div className="office-drop" data-over={dropOver || undefined} data-testid="office-drop"
                onDragEnter={(e) => { e.preventDefault(); setDropOver(true); }}
                onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; }}
                onDragLeave={() => setDropOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDropOver(false);
                  const image = draggedImage();
                  setDraggedImage(null);
                  if (image) void dropImage(image);
                }}>
                <span><IconPhotoPlus size={20} stroke={1.6} />Отпустите — «{dragged.name}» встанет на текущий слайд</span>
              </div>
            )}
          </div>
        </>
      )}
      {!actionsTarget && <Group justify="flex-end" p="xs">{actions}</Group>}
    </Stack>
  );
}