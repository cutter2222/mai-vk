import { createTheme, rem, type MantineColorsTuple } from "@mantine/core";

/**
 * Дизайн-система сервиса: светлая база, один синий акцент, холодные тени.
 *
 * Перенесена с дизайн-системы teplo.ai (там акцент тёплый, #F39C2A) с заменой бренда на
 * синий: инструмент работает с корпоративными шаблонами, и тёплый оранжевый спорил бы с
 * их оформлением. Правила те же и обязательны:
 *
 * 1. Поверхности больше 64 px — только нейтральные (`page`, `card`) или холодные
 *    (`sky`, `mist`). Акцент крупной заливкой не кладётся: он для главного действия,
 *    выделения и ссылок.
 * 2. Цвета состояний (`success`, `danger`) — текст и иконки, никогда не заливка.
 * 3. Тени только из холодной семьи ниже. На цветной поверхности тень грязнит — там
 *    разделяет волосяная рамка, а не тень.
 * 4. Одно главное действие на экран. Второе по важности — нейтральной кнопкой.
 *
 * Токены дублируются CSS-переменными в `app/globals.css`, чтобы их видели и Mantine,
 * и собственная разметка.
 */

/** Синий акцент: главное действие, выбор, ссылки. Белый текст на 6-м шаге даёт 4,6:1. */
const brand: MantineColorsTuple = [
  "#eef4ff",
  "#dbe7ff",
  "#b8ceff",
  "#8fb1fb",
  "#5b8ef5",
  "#3a7bf0",
  "#1f6feb",
  "#1a5cc4",
  "#174ea3",
  "#133f85",
];

/** Нейтральная шкала: поверхности, линии, вторичный текст. */
const ink: MantineColorsTuple = [
  "#f7f8fa",
  "#f1f2f5",
  "#eceef2",
  "#dfe2e8",
  "#c6cad3",
  "#9aa0ab",
  "#6b7280",
  "#4b5160",
  "#2c313c",
  "#1a1a1a",
];

/** Спокойный зелёный и терракота: только текст и иконки состояний. */
const success: MantineColorsTuple = [
  "#eef5f0",
  "#dcebe1",
  "#b6d6c2",
  "#8dbfa1",
  "#6aab86",
  "#529b74",
  "#43946a",
  "#2e7d4f",
  "#246a42",
  "#185634",
];

const danger: MantineColorsTuple = [
  "#fdf0ed",
  "#fadfd9",
  "#f2bdb0",
  "#e99885",
  "#e17a61",
  "#dc674b",
  "#d95d3e",
  "#c0392b",
  "#ab3025",
  "#94271d",
];

export const theme = createTheme({
  primaryColor: "brand",
  primaryShade: 6,
  colors: { brand, ink, success, danger, gray: ink },
  white: "#ffffff",
  black: "#1a1a1a",
  defaultRadius: "md",
  // Шкала радиусов teplo: 12 · 16 · 24 · 32. Мелкие элементы — 8.
  radius: { xs: rem(8), sm: rem(12), md: rem(16), lg: rem(24), xl: rem(32) },
  fontFamily:
    '"Golos Text Variable", "Golos Text", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  headings: {
    fontFamily:
      '"Golos Text Variable", "Golos Text", system-ui, -apple-system, sans-serif',
    fontWeight: "700",
    sizes: {
      // Плотная посадка заголовков: как `h-display` / `h-section` в исходной системе.
      h1: { fontSize: rem(34), lineHeight: "1.08", fontWeight: "700" },
      h2: { fontSize: rem(24), lineHeight: "1.15", fontWeight: "700" },
      h3: { fontSize: rem(18), lineHeight: "1.25", fontWeight: "600" },
      h4: { fontSize: rem(15), lineHeight: "1.3", fontWeight: "600" },
    },
  },
  fontSizes: { xs: rem(12), sm: rem(13), md: rem(15), lg: rem(17), xl: rem(20) },
  lineHeights: { xs: "1.4", sm: "1.45", md: "1.55", lg: "1.55", xl: "1.4" },
  // Холодная семья теней: тёплые на синем акценте выглядят грязно.
  shadows: {
    xs: "0 1px 2px rgba(20, 45, 80, 0.06)",
    sm: "0 1px 2px rgba(20, 45, 80, 0.05), 0 4px 10px rgba(20, 45, 80, 0.06)",
    md: "0 1px 3px rgba(20, 45, 80, 0.06), 0 8px 20px rgba(20, 45, 80, 0.08)",
    lg: "0 2px 6px rgba(20, 45, 80, 0.07), 0 16px 32px rgba(20, 45, 80, 0.1)",
    xl: "0 4px 10px rgba(20, 45, 80, 0.08), 0 24px 48px rgba(20, 45, 80, 0.12)",
  },
  components: {
    Card: { defaultProps: { withBorder: true, shadow: "none", padding: "lg", radius: "lg" } },
    Paper: { defaultProps: { withBorder: true, shadow: "none", radius: "lg" } },
    Badge: {
      defaultProps: { variant: "light", radius: "sm" },
      styles: { root: { textTransform: "none", letterSpacing: 0, fontWeight: 500 } },
    },
    Button: {
      defaultProps: { radius: "md" },
      styles: { root: { fontWeight: 600, letterSpacing: "-0.01em" } },
    },
    ActionIcon: { defaultProps: { radius: "md" } },
    Tooltip: { defaultProps: { withArrow: false, openDelay: 300, radius: "sm" } },
    Menu: {
      defaultProps: { shadow: "md", radius: "md" },
      styles: { dropdown: { border: "1px solid var(--mantine-color-ink-2)" } },
    },
    Modal: { defaultProps: { radius: "lg", overlayProps: { opacity: 0.35, blur: 2 } } },
    SegmentedControl: { defaultProps: { radius: "md" } },
    Alert: { defaultProps: { radius: "md", variant: "light" } },
    Accordion: { defaultProps: { radius: "md" } },
    Notification: { defaultProps: { radius: "md" } },
    Progress: { defaultProps: { radius: "xl" } },
    Tabs: { defaultProps: { radius: "md" } },
  },
});
