export const DESIGN_MODES = [
  { value: "template_only", label: "По шаблону" },
  { value: "mixed", label: "Смешанный" },
  { value: "all_new", label: "Все слайды новые" },
] as const;


export function isDesignModeReply(text: string): boolean {
  return DESIGN_MODES.some((mode) => mode.label.toLocaleLowerCase() === text.trim().toLocaleLowerCase().replace(/[.! ]+$/, ""));
}