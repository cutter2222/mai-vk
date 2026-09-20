/**
 * Знак шаблона в колоде.
 *
 * Шаблон приносит свой логотип: у VK Tech это 48 постоянных элементов вида `logo` на макетах.
 * На слайде такого объекта нет — он нарисован макетом, и правкой слайда его не снять. Другому
 * подразделению чужой знак делает шаблон непригодным, поэтому колода несёт свойство
 * `template_logo`: `drop` снимает логотипы со всех макетов, мастеров и слайдов при сборке.
 */

import type { TemplateProfile } from "@/lib/api/types";

/** Сколько логотипов шаблон ставит на макеты: 0 — убирать нечего, кнопка не нужна. */
export function countTemplateLogos(profile: TemplateProfile | null | undefined): number {
  return (profile?.fixed_elements ?? []).filter((f) => f.kind === "logo").length;
}
