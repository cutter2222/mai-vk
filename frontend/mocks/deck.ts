/**
 * ComposedDeck заглушки: описание собранной колоды по числу слайдов варианта с теми же
 * объектами, что у заглушки сервера (заголовок «2», текст «3», логотип «5»), эхом ручных
 * правок ревизии и порядком слайдов. Нужно холсту редактора и проверкам патча.
 */

import type { ComposedDeck, Override } from "@/lib/api/types";

import deckExample from "./data/composed_deck.json";
import { overridesAt, slideIdsAt, type MockGeneration } from "./state";

type DeckSlide = ComposedDeck["slides"][number];
type DeckObject = DeckSlide["objects"][number];

const TITLE_FONT = { family: "Play", size_pt: 32, bold: true, color: "#000000" };
const BODY_FONT = { family: "Arial", size_pt: 16, bold: false, color: "#202020" };

function textObject(id: string, name: string, slot: string, bbox: DeckObject["bbox"], text: string, font: typeof TITLE_FONT, z: number): DeckObject {
  return {
    object_id: id,
    name,
    kind: "text",
    geometry: "rect",
    bbox,
    z_order: z,
    slot_id: slot,
    slot_kind: slot,
    block_kind: slot,
    source_object_id: id,
    role: "content",
    content_source: "plan",
    text: {
      plain: text,
      paragraphs: [{ text, level: 0, bullet: false, align: "left" }],
      computed_style: { font: { ...font } },
      insets: { left: 0.006, top: 0.004, right: 0.006, bottom: 0.004 },
      autofit: "none",
    },
  };
}

function applyEcho(obj: DeckObject, overrides: Override[]): DeckObject {
  const mine = overrides.filter((o) => o.target?.object_id === obj.object_id);
  if (mine.length === 0) return obj;
  const out: DeckObject = { ...obj, user_overrides: mine.map((o) => structuredClone(o)) };
  for (const o of mine) {
    if (o.op === "text" && o.text !== undefined && out.text) {
      out.text = { ...out.text, plain: o.text, paragraphs: o.text.split("\n").map((line) => ({ text: line, level: 0, bullet: false, align: out.text?.paragraphs?.[0]?.align ?? "left" })) };
      out.content_source = "user";
    }
    if (o.op === "style" && o.style && out.text) {
      const font = { ...(out.text.computed_style?.font ?? {}), ...(o.style.font ?? {}) };
      out.text = { ...out.text, computed_style: { ...(out.text.computed_style ?? {}), font }, paragraphs: (out.text.paragraphs ?? []).map((p) => ({ ...p, ...(o.style?.align ? { align: o.style.align } : {}) })) };
    }
    if (o.op === "geometry" && o.geometry?.bbox) out.bbox = { ...o.geometry.bbox };
    if (o.op === "picture" && o.picture) {
      out.picture = { ...(out.picture ?? {}), asset_id: o.picture.source.asset_id ?? "asset_logo", fit: o.picture.fit ?? "contain", recolored: Boolean(o.picture.color), origin: o.picture.source.kind === "template" ? "template" : "content" };
      out.content_source = "user";
    }
  }
  return out;
}

export function buildDeck(g: MockGeneration, variantId: string, revision: number, titles: string[]): ComposedDeck {
  const v = g.variants.find((x) => x.variant_id === variantId);
  const ids = slideIdsAt(v, revision);
  const echo = overridesAt(v, revision);
  const prefix = `${variantId}/r${revision}/`;
  const slides: DeckSlide[] = ids.map((slideId, index) => {
    const base = Number(slideId.slice(1)) - 1;
    let title = titles[base] ?? `Слайд ${base + 1}`;
    for (const rec of v?.revisions ?? []) {
      if (rec.revision <= revision && rec.titles?.[slideId] !== undefined && !(rec.overrides?.[slideId]?.some((o) => o.op === "text"))) title = rec.titles[slideId];
    }
    const overrides = echo[slideId] ?? [];
    const logo: DeckObject = {
      object_id: "5",
      name: "Logo",
      kind: "picture",
      bbox: { x: 0.87, y: 0.9, width: 0.08, height: 0.06 },
      z_order: 0,
      role: "fixed",
      source_object_id: "5",
      content_source: "template",
      picture: { asset_id: "asset_logo", natural_width_px: 64, natural_height_px: 64, origin: "template", fit: "as_is" },
    };
    const objects: DeckObject[] = [
      textObject("2", "Title 1", "title", { x: 0.05, y: 0.06, width: 0.9, height: 0.12 }, title, TITLE_FONT, 1),
      textObject("3", "Body 2", "body", { x: 0.05, y: 0.3, width: 0.6, height: 0.4 }, `Вариант ${variantId}: тезис слайда и пояснение к нему`, BODY_FONT, 2),
      logo,
    ].map((o) => applyEcho(o, overrides));
    const bg = overrides.find((o) => o.op === "background")?.background;
    const slide: DeckSlide = {
      slide_id: slideId,
      index,
      pptx_slide_part: `ppt/slides/slide${index + 1}.xml`,
      layout_id: index === 0 ? "slideLayout2" : "slideLayout6",
      pattern_id: index === 0 ? "pat_title" : "pat_bullets_4",
      background: bg ? (bg.kind === "solid" ? { kind: "solid", color: bg.color } : bg.kind === "image" ? { kind: "image", asset_id: bg.source?.asset_id ?? "asset_logo" } : { kind: "inherited" }) : { kind: "inherited" },
      objects,
      title,
      removed_object_ids: [],
    };
    if (overrides.length > 0) {
      slide.overrides = structuredClone(overrides);
      slide.overrides_dropped = [];
    }
    return slide;
  });
  const sample = deckExample as unknown as ComposedDeck;
  return {
    ...sample,
    deck_id: `deck_${g.job_id}_${variantId}_r${revision}`,
    job_id: g.job_id,
    variant_id: variantId,
    revision,
    plan_id: g.plan.plan_id,
    template_id: g.template.template_id,
    pptx_artifact: `${prefix}deck.pptx`,
    slides: slides as ComposedDeck["slides"],
    assets: [{ asset_id: "asset_logo", media_path: "ppt/media/image1.png", sha256: "0".repeat(64), content_type: "image/png", origin: "template", shared_with_template: true, artifact: `${prefix}media/asset_logo.png` }],
    warnings: [],
    stats: { slides: slides.length, objects: slides.length * 3, text_objects: slides.length * 2, pictures: slides.length, tables: 0, charts: 0, diagrams: 0, removed_objects: 0 },
  };
}
