/**
 * Чистые функции визуального редактора: черновик ручных правок (список overrides слайда,
 * как в контракте slide_patch), их применение к описанию слайда ComposedDeck для живого
 * холста, слияние, сброс и сводка для подписи. Ни сети, ни состояния React.
 */

import type { ComposedDeck, Override, SlidePatch } from "@/lib/api/types";

export type DeckSlide = ComposedDeck["slides"][number];
export type DeckObject = DeckSlide["objects"][number];
export type OverrideOp = Override["op"];
export type PatchSlide = SlidePatch["slides"][number];

/** Адреса картинок для холста: ресурс шаблона, пакета или свой файл черновика. */
export interface AssetResolver {
  /** Адрес по источнику правки; null — источник ещё не известен холсту (покажется прежняя картинка). */
  sourceUrl: (source: NonNullable<Override["picture"]>["source"]) => string | null;
}

/** Порядок применения совпадает с композером: текст → стиль → положение → картинка, фон последним. */
const OP_ORDER: Record<OverrideOp, number> = { text: 0, style: 1, geometry: 2, picture: 3, background: 4 };

export function sortOverrides(list: Override[]): Override[] {
  return list.map((o, i) => [o, i] as const).sort((a, b) => OP_ORDER[a[0].op] - OP_ORDER[b[0].op] || a[1] - b[1]).map(([o]) => o);
}

/** Слайд ComposedDeck с наложенным черновиком: то, что рисует холст до применения на сервере. */
export function applyOverrides(slide: DeckSlide, overrides: Override[], resolve?: AssetResolver): DeckSlide {
  if (overrides.length === 0) return slide;
  const objects = slide.objects.map((o) => ({ ...o }));
  const byId = new Map(objects.map((o) => [o.object_id, o]));
  let background = slide.background;
  let backgroundUrl: string | null | undefined;
  for (const ov of sortOverrides(overrides)) {
    if (ov.op === "background") {
      const spec = ov.background;
      if (!spec) continue;
      if (spec.kind === "inherited") background = { kind: "inherited" };
      else if (spec.kind === "solid") background = { kind: "solid", color: spec.color };
      else {
        background = { kind: "image", asset_id: spec.source?.asset_id ?? spec.source?.file_id };
        backgroundUrl = spec.source ? (resolve?.sourceUrl(spec.source) ?? null) : null;
      }
      continue;
    }
    const id = ov.target?.object_id ?? "";
    const obj = byId.get(id);
    if (!obj) continue;
    if (ov.op === "text" && ov.text !== undefined) {
      const lines = ov.text.split("\n");
      const first = obj.text?.paragraphs?.[0];
      const paragraphs = lines.map((line) => ({ ...(first ?? {}), text: line }));
      obj.text = { ...(obj.text ?? {}), plain: ov.text, paragraphs };
      obj.content_source = "user";
    } else if (ov.op === "style" && ov.style) {
      const font = ov.style.font ?? {};
      const align = ov.style.align;
      const merge = (style: NonNullable<DeckObject["text"]>["computed_style"] | undefined) => ({
        ...(style ?? {}),
        font: { ...(style?.font ?? {}), ...stripUndefined(font) },
      });
      const text = obj.text ?? { plain: "" };
      obj.text = {
        ...text,
        computed_style: merge(text.computed_style),
        paragraphs: (text.paragraphs ?? []).map((p) => ({
          ...p,
          ...(align ? { align } : {}),
          ...(p.style ? { style: merge(p.style) } : {}),
        })),
      };
    } else if (ov.op === "geometry" && ov.geometry?.bbox) {
      obj.bbox = { ...ov.geometry.bbox };
    } else if (ov.op === "picture" && ov.picture) {
      const source = ov.picture.source;
      const url = resolve?.sourceUrl(source) ?? null;
      obj.picture = {
        ...(obj.picture ?? {}),
        asset_id: source.asset_id ?? source.file_id ?? obj.picture?.asset_id,
        fit: ov.picture.fit ?? (obj.slot_kind === "icon" ? "contain" : "cover"),
        recolored: Boolean(ov.picture.color),
        crop: undefined,
      };
      obj.content_source = "user";
      // Адрес и цвет перекраски живут вне контракта: холст читает их из расширения объекта.
      (obj as DeckObject & { _draft_url?: string | null; _draft_color?: string })._draft_url = url;
      (obj as DeckObject & { _draft_color?: string })._draft_color = ov.picture.color;
    }
    obj.user_overrides = [...(obj.user_overrides ?? []), ov];
  }
  const out: DeckSlide & { _draft_bg_url?: string | null } = { ...slide, objects, background };
  if (backgroundUrl !== undefined) out._draft_bg_url = backgroundUrl;
  return out;
}

function stripUndefined<T extends Record<string, unknown>>(value: T): Partial<T> {
  const out: Partial<T> = {};
  for (const [k, v] of Object.entries(value)) if (v !== undefined) (out as Record<string, unknown>)[k] = v;
  return out;
}

/** Правка в черновике: один op каждого вида на объект (фон — на слайд), новая заменяет прежнюю. */
export function mergeDraft(list: Override[], op: Override): Override[] {
  const key = draftKey(op);
  const rest = list.filter((o) => draftKey(o) !== key);
  return [...rest, op];
}

export function draftKey(op: Override): string {
  return `${op.op}:${op.op === "background" ? "" : (op.target?.object_id ?? "")}`;
}

/** Убирает из черновика все правки объекта (или фона при пустом идентификаторе). */
export function resetObject(list: Override[], objectId: string | null): Override[] {
  return list.filter((o) => (objectId === null ? o.op !== "background" : o.target?.object_id !== objectId));
}

/** Правка одного вида для объекта из списка. */
export function findOverride(list: Override[], op: OverrideOp, objectId: string | null): Override | undefined {
  return list.find((o) => o.op === op && (op === "background" || o.target?.object_id === objectId));
}

/** Одинаковые списки правок с точностью до порядка внутри вида. */
export function sameOverrides(a: Override[], b: Override[]): boolean {
  const norm = (list: Override[]) => JSON.stringify(sortOverrides(list).map((o) => ({ ...o })));
  return norm(a) === norm(b);
}

const SLOT_LABELS: Record<string, string> = {
  title: "заголовок",
  subtitle: "подзаголовок",
  body: "текст",
  bullets: "список",
  number: "показатель",
  label: "подпись",
  caption: "подпись",
  date: "дата",
  name: "имя",
  position: "должность",
  image: "картинка",
  icon: "иконка",
  qr: "QR-код",
  code: "код",
};

/** Человеческое имя объекта для панели и сводки: по виду слота, иначе по виду объекта. */
export function objectLabel(obj: DeckObject | undefined): string {
  if (!obj) return "объект";
  const slot = obj.slot_kind ?? obj.block_kind;
  if (slot && SLOT_LABELS[slot]) return SLOT_LABELS[slot];
  switch (obj.kind) {
    case "picture":
      return "картинка";
    case "text":
    case "placeholder_empty":
      return "текст";
    case "table":
      return "таблица";
    case "chart":
      return "диаграмма";
    case "group":
      return "группа";
    case "connector":
      return "линия";
    default:
      return obj.name?.trim() ? `«${obj.name.trim()}»` : "фигура";
  }
}

/** Что изменится при применении: «слайд 3: заголовок (текст, кегль); фон». */
export function describeDraft(overrides: Override[], slide: DeckSlide): string {
  const byObject = new Map<string, string[]>();
  for (const ov of sortOverrides(overrides)) {
    const id = ov.op === "background" ? "" : (ov.target?.object_id ?? "");
    const item = byObject.get(id) ?? [];
    if (ov.op === "text") item.push("текст");
    else if (ov.op === "style") {
      const f = ov.style?.font ?? {};
      if (f.size_pt !== undefined) item.push(`кегль ${f.size_pt}`);
      if (f.family) item.push(`гарнитура ${f.family}`);
      if (f.bold !== undefined) item.push(f.bold ? "жирный" : "обычный");
      if (f.italic !== undefined) item.push(f.italic ? "курсив" : "без курсива");
      if (f.color) item.push(`цвет ${f.color.toUpperCase()}`);
      if (ov.style?.align) item.push({ left: "по левому краю", center: "по центру", right: "по правому краю", justify: "по ширине" }[ov.style.align]);
    } else if (ov.op === "geometry") item.push("положение");
    else if (ov.op === "picture") {
      const src = ov.picture?.source;
      const kind = src?.kind === "template" ? "из шаблона" : src?.kind === "package" ? "из материалов" : "своя";
      item.push(`картинка ${kind}${src?.name ? ` (${src.name})` : ""}${ov.picture?.color ? ", перекраска" : ""}`);
    } else if (ov.op === "background") {
      const b = ov.background;
      item.push(b?.kind === "solid" ? `фон ${b.color?.toUpperCase() ?? ""}`.trim() : b?.kind === "image" ? "фон-картинка" : "фон как в макете");
    }
    byObject.set(id, item);
  }
  const parts: string[] = [];
  for (const [id, items] of byObject) {
    if (id === "") parts.push(items.join(", "));
    else parts.push(`${objectLabel(slide.objects.find((o) => o.object_id === id))}: ${items.join(", ")}`);
  }
  return parts.join("; ");
}

/** Список слайдов патча: только те, чей черновик отличается от эха ревизии. */
export function patchSlides(drafts: Record<string, Override[]>, deck: ComposedDeck): PatchSlide[] {
  const out: PatchSlide[] = [];
  for (const slide of deck.slides) {
    const draft = drafts[slide.slide_id];
    if (draft === undefined) continue;
    if (sameOverrides(draft, slide.overrides ?? [])) continue;
    out.push({ slide_id: slide.slide_id, overrides: sortOverrides(draft) });
  }
  return out;
}
