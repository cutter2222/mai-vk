/**
 * Заглушка извлечения брифа из сообщения: детерминированные правила по русским формулировкам.
 * В рабочем режиме это делает модель на сервере; здесь важно лишь, чтобы интерфейс получал те же поля.
 */

import type { BriefExtractResponse } from "@/lib/api/client";

type Purpose = NonNullable<BriefExtractResponse["brief"]["purpose"]>;
type Variant = "compact" | "balanced" | "detailed";
const GENERATE_RE = /сгенерир|запусти|собери|сделай|построй|начина/i;
const EDIT_RE = /поменя[йть]|перестав|местами|удали|убери|добавь слайд|переимен/i;

const PURPOSES: Array<[RegExp, Purpose]> = [
  [/отч[её]т/i, "report"],
  [/инициатив/i, "initiative"],
  [/фич[аиеу]/i, "feature"],
  [/продукт|сервис/i, "product"],
  [/проект/i, "project"],
];

const clean = (s: string) => s.replace(/^[\s,:—–-]+|[\s,.;:!?—–-]+$/g, "").trim();

export function extractBrief(text: string): BriefExtractResponse {
  const understood: string[] = [];
  const brief: BriefExtractResponse["brief"] = {};
  let slide_count: BriefExtractResponse["slide_count"];
  let variants: Variant[] | undefined;
  const t = text.replace(/\s+/g, " ").trim();
  const intent: BriefExtractResponse["intent"] = EDIT_RE.test(t) ? "edit" : GENERATE_RE.test(t) ? "generate" : "none";
  const base = { schema_version: "1.2" as const, intent, source: "heuristic" as const };
  if (!t) return { ...base, brief, understood };

  const purpose = PURPOSES.find(([re]) => re.test(t))?.[1];
  if (purpose) {
    brief.purpose = purpose;
    understood.push("purpose");
  }

  const quoted = t.match(/«([^»]{3,80})»|"([^"]{3,80})"/);
  const about = t.match(/(?:презентаци[юяи]|питч|отч[её]т|доклад|слайды)\s+(?:про|о|об|обо|на тему|по)\s+(.+?)(?=,| для | чтобы | цель| на \d| в \d|\.|$)/i);
  const title = quoted?.[1] ?? quoted?.[2] ?? about?.[1];
  if (title) {
    brief.title = clean(title).replace(/^./, (c) => c.toUpperCase());
    understood.push("title");
  }

  // \b в JS не знает кириллицы, поэтому граница слова задаётся пробелом или началом строки.
  const audience = t.match(/(?:^|\s)для\s+(.+?)(?=,| чтобы| цель| на \d| в \d|\.|$)/i);
  if (audience && !/слайд/i.test(audience[1])) {
    brief.audience = clean(audience[1]);
    understood.push("audience");
  }

  const goal = t.match(/(?:чтобы|цель[:\s—–-]+)\s*(.+?)(?=\.|,\s*(?:тон|язык|слайд)|$)/i);
  if (goal) {
    brief.goal = clean(goal[1]);
    understood.push("goal");
  }

  const tone = t.match(/тон[:\s—–-]+(.+?)(?=\.|,|$)/i);
  if (tone) {
    brief.tone = clean(tone[1]);
    understood.push("tone");
  }

  if (/english|англий/i.test(t)) {
    brief.language = "en";
    understood.push("language");
  }

  const must = t.match(/обязательно\s+(?:включи(?:ть)?|добав(?:ь|ить)|нужн[ыоа]|упомян(?:и|уть))\s+(.+?)(?=\.|$)/i);
  if (must) {
    brief.must_include = must[1].split(/,| и /).map(clean).filter(Boolean);
    understood.push("must_include");
  }
  const avoid = t.match(/(?:без|не\s+(?:надо|нужно)|избега(?:й|ть))\s+(.+?)(?=\.|,|$)/i);
  if (avoid && !/вариант/i.test(avoid[1])) {
    brief.avoid = avoid[1].split(/,| и /).map(clean).filter(Boolean);
    understood.push("avoid");
  }

  const range = t.match(/(\d{1,2})\s*[-–—]\s*(\d{1,2})\s*слайд/i);
  const exact = t.match(/(?:ровно\s+)?(\d{1,2})\s*слайд/i);
  if (range) {
    slide_count = { min: Number(range[1]), max: Number(range[2]) };
    understood.push("slide_count");
  } else if (exact) {
    slide_count = { exact: Number(exact[1]) };
    understood.push("slide_count");
  }

  const ALL: Variant[] = ["compact", "balanced", "detailed"];
  const wanted = ALL.filter((v) => ({ compact: /компактн/i, balanced: /сбалансир/i, detailed: /подробн/i })[v].test(t));
  if (/без\s+(компактн|сбалансир|подробн)/i.test(t)) {
    variants = ALL.filter((v) => !wanted.includes(v));
    understood.push("variants");
  } else if (/только\s+(компактн|сбалансир|подробн)/i.test(t) && wanted.length) {
    variants = wanted;
    understood.push("variants");
  }

  return { ...base, brief, slide_count, variants, understood };
}
