import { createTheme, rem } from "@mantine/core";

// Светлая нейтральная тема: интерфейс не спорит с оформлением презентации.
export const theme = createTheme({
  primaryColor: "blue",
  defaultRadius: "md",
  fontFamily:
    'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
  headings: { fontWeight: "600" },
  fontSizes: { xs: rem(12), sm: rem(13), md: rem(14), lg: rem(16), xl: rem(18) },
  components: {
    Card: { defaultProps: { withBorder: true, shadow: "none", padding: "md" } },
    Paper: { defaultProps: { withBorder: true, shadow: "none" } },
  },
});
