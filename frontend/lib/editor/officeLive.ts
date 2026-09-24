/**
 * Текущий слайд и выделение живого редактора ONLYOFFICE 9.3.1. Публичного способа узнать их нет
 * (коннектор — в платной редакции), поэтому используется внутренний API редактора в том же
 * окне, что и для сохранения (`officeSave.ts`): те же события, на которые подписаны его
 * строка состояния («Слайд 3 из 10») и панель свойств. Номер фигуры в PPTX редактор наружу не
 * отдаёт — выделенный объект описывается именем и рамкой в миллиметрах, сервер находит его в
 * сохранённой копии сам. Нет нужных методов (другая версия) — моста нет, чат работает как раньше.
 */

export interface LiveObject {
  name: string;
  /** Текст объекта из карты сохранённой копии — подпись для чата. */
  label?: string;
  kind: "shape" | "image" | "chart" | "table";
  /** Рамка в миллиметрах, как в панели свойств ONLYOFFICE. */
  box?: { x: number; y: number; width: number; height: number };
}

export interface LiveSelection {
  /** Номер слайда с единицы. */
  slide: number;
  count: number;
  /** Выделенные объекты; пусто — выбран только слайд. */
  objects: LiveObject[];
}

type Value = Record<string, ((...args: unknown[]) => unknown) | undefined>;
type Element = { get_ObjectType: () => number; get_ObjectValue: () => Value | null };
type LiveApi = {
  getCurrentPage: () => number;
  getCountPages: () => number;
  getSelectedElements: () => Element[] | null;
  asc_getSelectedDrawingObjectsCount?: () => number;
  goToPage?: (index: number) => void;
  asc_registerCallback: (name: string, callback: (...args: unknown[]) => void) => void;
  asc_unregisterCallback: (name: string, callback: (...args: unknown[]) => void) => void;
};
type LiveWindow = Window & {
  PE?: { getController: (name: string) => { getApi: () => LiveApi } };
  Asc?: { c_oAscTypeSelectElement?: Record<string, number> };
};

function liveApi(frame: HTMLIFrameElement | null): { api: LiveApi; types: Record<string, number> } | null {
  try {
    const win = frame?.contentWindow as LiveWindow | null;
    const api = win?.PE?.getController("Viewport").getApi();
    if (!api || [api.getCurrentPage, api.getCountPages, api.getSelectedElements, api.asc_registerCallback, api.asc_unregisterCallback].some((fn) => typeof fn !== "function")) return null;
    return { api, types: win?.Asc?.c_oAscTypeSelectElement ?? {} };
  } catch {
    return null;
  }
}

const call = (value: Value, ...names: string[]): unknown => {
  for (const name of names) {
    const fn = value[name];
    if (typeof fn === "function") {
      try { return fn.call(value); } catch { /* следующее имя */ }
    }
  }
  return undefined;
};

function describe(api: LiveApi, types: Record<string, number>): LiveSelection {
  const kinds: Array<[number | undefined, LiveObject["kind"]]> = [[types.Shape ?? 6, "shape"], [types.Image, "image"], [types.Chart, "chart"], [types.Table, "table"]];
  const objects: LiveObject[] = [];
  for (const element of api.getSelectedElements() ?? []) {
    const kind = kinds.find(([code]) => code !== undefined && code === element.get_ObjectType())?.[1];
    const value = kind ? element.get_ObjectValue() : null;
    if (!kind || !value) continue;
    const name = String(call(value, "asc_getName", "get_Name") ?? "").trim();
    const position = call(value, "asc_getPosition", "get_Position") as Value | undefined;
    const x = position ? Number(call(position, "get_X", "asc_getX")) : NaN;
    const y = position ? Number(call(position, "get_Y", "asc_getY")) : NaN;
    const width = Number(call(value, "asc_getWidth", "get_Width"));
    const height = Number(call(value, "asc_getHeight", "get_Height"));
    const box = [x, y, width, height].every(Number.isFinite) ? { x, y, width, height } : undefined;
    objects.push({ name, kind, ...(box ? { box } : {}) });
  }
  // Несколько фигур ONLYOFFICE сводит в одну запись со свойствами общей рамки — имени у неё нет.
  const several = (api.asc_getSelectedDrawingObjectsCount?.() ?? objects.length) > 1;
  return {
    slide: api.getCurrentPage() + 1,
    count: api.getCountPages(),
    objects: several ? objects.map((o) => ({ ...o, name: "" })) : objects,
  };
}

/** Подписка на смену слайда и выделения; `null`, если мост недоступен. Отписка — результат. */
export function watchOfficeSelection(frame: HTMLIFrameElement | null, onChange: (value: LiveSelection) => void): (() => void) | null {
  const live = liveApi(frame);
  if (!live) return null;
  const { api, types } = live;
  let last = "";
  let queued = false;
  const emit = () => {
    // Одно действие присылает события пачкой (выделение приходит по три раза): отдаём итог.
    if (queued) return;
    queued = true;
    setTimeout(() => {
      queued = false;
      let value: LiveSelection;
      try { value = describe(api, types); } catch { return; }
      const key = JSON.stringify(value);
      if (key === last) return;
      last = key;
      onChange(value);
    }, 0);
  };
  const events = ["asc_onCurrentPage", "asc_onFocusObject", "asc_onCountPages"];
  events.forEach((name) => api.asc_registerCallback(name, emit));
  emit();
  return () => events.forEach((name) => { try { api.asc_unregisterCallback(name, emit); } catch { /* редактор закрыт */ } });
}

/** Открыть слайд `slide` (с единицы) в живом редакторе; false — не вышло. */
export function goToOfficeSlide(frame: HTMLIFrameElement | null, slide: number): boolean {
  const live = liveApi(frame);
  if (!live?.api.goToPage) return false;
  try {
    live.api.goToPage(slide - 1);
    return true;
  } catch {
    return false;
  }
}
