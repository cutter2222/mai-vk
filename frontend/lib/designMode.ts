export const DESIGN_MODES = [
  { value: "template_only", label: "По шаблону" },
  { value: "mixed", label: "Смешанный" },
  { value: "all_new", label: "Все слайды новые" },
] as const;

export const DESIGN_MODE_QUESTION = "Как оформить презентацию? По шаблону — сохранить макеты и расположение блоков; смешанный — использовать макеты и добавлять новые композиции; все слайды новые — новые композиции, включая титул и финал, в стиле исходника. Подробность содержания выбирается отдельно.";

export function isDesignModeReply(text: string): boolean {
  return DESIGN_MODES.some((mode) => mode.label.toLocaleLowerCase() === text.trim().toLocaleLowerCase().replace(/[.! ]+$/, ""));
}