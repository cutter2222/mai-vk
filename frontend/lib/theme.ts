import { createTheme, rem, type MantineColorsTuple } from "@mantine/core";

/**
 * Спокойная нейтральная тема: интерфейс не спорит с оформлением презентации.
 * Основные действия графитовые, один приглушённый синий для выбора и ссылок, без теней,
 * бейджи без капса. Шрифт Golos Text с полноценной кириллицей поставляется с приложением.
 */

const graphite: MantineColorsTuple = ["#f3f4f6", "#e4e6ea", "#c9ccd3", "#a7abb5", "#7f8491", "#5c616d", "#41454f", "#2c2f37", "#1d1f25", "#121317"];

// Приглушённый синий вместо яркого дефолтного: выделение, активные состояния, ссылки.
const blue: MantineColorsTuple = ["#eef3fa", "#dbe6f4", "#b8cce9", "#92b0dc", "#7196cf", "#5b84c5", "#4d77bb", "#4067a6", "#375a93", "#2c4c7f"];

const gray: MantineColorsTuple = ["#f6f7f9", "#eeeff2", "#e2e4e9", "#d1d4db", "#b3b7c1", "#8c909c", "#6b6f7b", "#4e525c", "#33363d", "#1f2126"];

export const theme = createTheme({
  primaryColor: "graphite",
  primaryShade: 8,
  colors: { graphite, blue, gray },
  defaultRadius: "md",
  radius: { xs: rem(4), sm: rem(6), md: rem(10), lg: rem(14), xl: rem(20) },
  fontFamily: '"Golos Text Variable", "Golos Text", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif',
  headings: { fontWeight: "600", fontFamily: '"Golos Text Variable", "Golos Text", -apple-system, sans-serif' },
  fontSizes: { xs: rem(12), sm: rem(13), md: rem(14), lg: rem(16), xl: rem(18) },
  shadows: { xs: "none", sm: "none", md: "none", lg: "none", xl: "none" },
  black: "#1d1f25",
  components: {
    Card: { defaultProps: { withBorder: true, shadow: "none", padding: "md", radius: "lg" } },
    Paper: { defaultProps: { withBorder: true, shadow: "none" } },
    Badge: {
      defaultProps: { variant: "light", radius: "sm" },
      styles: { root: { textTransform: "none", letterSpacing: 0, fontWeight: 500 } },
    },
    Button: { defaultProps: { radius: "md" }, styles: { root: { fontWeight: 500 } } },
    ActionIcon: { defaultProps: { radius: "md" } },
    Tooltip: { defaultProps: { withArrow: false, openDelay: 300, radius: "sm" } },
    Menu: { defaultProps: { shadow: "none", radius: "md" }, styles: { dropdown: { border: "1px solid var(--mantine-color-gray-3)" } } },
    Modal: { defaultProps: { radius: "lg", overlayProps: { opacity: 0.35, blur: 2 } } },
    SegmentedControl: { defaultProps: { radius: "md" } },
    Alert: { defaultProps: { radius: "md", variant: "light" } },
    Accordion: { defaultProps: { radius: "md" } },
    Notification: { defaultProps: { radius: "md" } },
    Progress: { defaultProps: { radius: "xl" } },
  },
});
