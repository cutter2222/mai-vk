/** ONLYOFFICE 9.3.1 adapter for our same-origin /onlyoffice deployment.
 * DocsAPI.requestClose is a discard/close prompt, NOT a save operation.
 * Keep this version-specific SDK dependency isolated and fail closed if unavailable.
 */
type SaveApi = {
  asc_Save: (auto: boolean) => boolean;
  isDocumentModified: () => boolean;
  asc_isDocumentCanSave: () => boolean;
  asc_registerCallback: (name: string, callback: (...args: number[]) => void) => void;
  asc_unregisterCallback: (name: string, callback: (...args: number[]) => void) => void;
};
type OfficeWindow = Window & {
  PE?: { getController: (name: string) => { getApi: () => SaveApi } };
  Asc?: { c_oAscAsyncAction: { Save: number } };
};

export async function flushOfficeFrame(frame: HTMLIFrameElement | null, signal: AbortSignal): Promise<void> {
  const unavailable = "Не удалось запустить сохранение ONLYOFFICE. Редактор оставлен открытым.";
  let api: SaveApi | undefined;
  let saveAction: number | undefined;
  try {
    const win = frame?.contentWindow as OfficeWindow | null;
    api = win?.PE?.getController("Viewport").getApi();
    saveAction = win?.Asc?.c_oAscAsyncAction.Save;
  } catch { throw new Error(unavailable); }
  if (!api || typeof saveAction !== "number" ||
    [api.asc_Save, api.isDocumentModified, api.asc_isDocumentCanSave,
      api.asc_registerCallback, api.asc_unregisterCallback].some(fn => typeof fn !== "function")) {
    throw new Error(unavailable);
  }
  const sdk = api;
  await new Promise<void>((resolve, reject) => {
    let saving = false;
    let ended = false;
    let started = false;
    let settled = false;
    let cleanSince: number | undefined;
    let lastSaveAt = 0;
    const cleanup = () => {
      clearInterval(poll);
      clearTimeout(timeout);
      signal.removeEventListener("abort", abort);
      // A detached/broken SDK must not prevent timer cleanup or Promise rejection.
      try { sdk.asc_unregisterCallback("asc_onEndAction", onEnd); } catch { /* SDK gone */ }
      try { sdk.asc_unregisterCallback("asc_onError", onError); } catch { /* SDK gone */ }
    };
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      cleanup();
      if (error) reject(error); else resolve();
    };
    const check = () => {
      if (settled || !started || (saving && !ended)) return;
      try {
        if (sdk.isDocumentModified() || sdk.asc_isDocumentCanSave()) {
          cleanSince = undefined;
          // Changes can arrive while the preceding save is in flight. Flush again,
          // but never overlap save actions or treat an acknowledgement as "clean".
          if ((ended || !saving) && Date.now() - lastSaveAt >= 300) {
            ended = false;
            started = false;
            lastSaveAt = Date.now();
            saving = sdk.asc_Save(true);
            started = true;
          }
          return;
        }
        // Debounce the clean SDK state, not the user's explicit save command.
        // A late dirty notification must reset this quiet window before close.
        cleanSince ??= Date.now();
        if (Date.now() - cleanSince >= 300) finish();
      } catch { finish(new Error(unavailable)); }
    };
    const onEnd = (_type: number, action: number) => {
      if (action === saveAction) { ended = true; queueMicrotask(check); }
    };
    const onError = () => finish(new Error("ONLYOFFICE не сохранил правки. Редактор оставлен открытым."));
    const abort = () => finish(new Error("Сохранение прервано: страница закрыта."));
    // Poll actual SDK state, not the asynchronous parent onDocumentStateChange flag.
    const poll = setInterval(check, 100);
    const timeout = setTimeout(() => finish(new Error("Сохранение не подтверждено. Редактор оставлен открытым; повторите завершение.")), 30000);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) { abort(); return; }
    try {
      sdk.asc_registerCallback("asc_onEndAction", onEnd);
      sdk.asc_registerCallback("asc_onError", onError);
      // Always ask the SDK to flush: fast collaboration can hide pending changes
      // from the parent's dirty flag. true skips an unnecessary force-save conversion;
      // disconnect then triggers the normal final callback to immutable storage.
      lastSaveAt = Date.now();
      saving = sdk.asc_Save(true);
      started = true;
      check();
    } catch { finish(new Error(unavailable)); }
  });
}