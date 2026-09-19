/**
 * Токены шаблона для панели свойств: палитра, шкала кеглей и гарнитуры профиля предлагаются
 * первыми; значение вне токенов разрешено и помечается «не из шаблона» (композер добавит
 * предупреждение override_off_template для аудита).
 */

import type { Override, TemplateProfile } from "@/lib/api/types";

export interface TemplateTokens {
  colors: Array<{ hex: string; role?: string }>;
  sizes: number[];
  families: string[];
}

export function templateTokens(profile: TemplateProfile | null | undefined): TemplateTokens {
  const tokens = profile?.design_tokens;
  const palette = (tokens?.colors?.palette ?? []).map((c) => ({ hex: c.hex.toUpperCase(), role: c.role }));
  const theme = Object.values(tokens?.colors?.theme ?? {}).filter((v): v is string => typeof v === "string").map((v) => v.toUpperCase());
  const seen = new Set<string>();
  const colors: TemplateTokens["colors"] = [];
  for (const c of [...palette, ...theme.map((hex) => ({ hex }))]) {
    if (seen.has(c.hex)) continue;
    seen.add(c.hex);
    colors.push(c);
  }
  const sizes = [...new Set((tokens?.typography?.scale ?? []).map((s) => s.size_pt))].sort((a, b) => b - a);
  const families = [...new Set([...(tokens?.typography?.fonts ?? []).map((f) => f.family), ...Object.values(tokens?.typography?.theme_fonts ?? {}).filter((v): v is string => typeof v === "string")])];
  return { colors, sizes, families };
}

/** Что в правке стиля выходит за токены шаблона; пустой массив — всё из шаблона. */
export function offTemplate(op: Override, tokens: TemplateTokens): string[] {
  if (op.op !== "style" || !op.style?.font) return [];
  const f = op.style.font;
  const out: string[] = [];
  if (f.family && tokens.families.length && !tokens.families.some((x) => x.toLowerCase() === f.family?.toLowerCase())) out.push(`гарнитура ${f.family}`);
  if (f.size_pt !== undefined && tokens.sizes.length && !tokens.sizes.includes(f.size_pt)) out.push(`кегль ${f.size_pt}`);
  if (f.color && tokens.colors.length && !tokens.colors.some((c) => c.hex === f.color?.toUpperCase())) out.push(`цвет ${f.color.toUpperCase()}`);
  return out;
}
