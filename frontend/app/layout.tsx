import "@mantine/core/styles.css";
import "@mantine/dropzone/styles.css";
import "@mantine/notifications/styles.css";
import "@fontsource-variable/golos-text";
// Play — шрифт шаблонов VK: холст редактора рисует текст слайда им же (OFL, без внешних запросов).
import "@fontsource/play/400.css";
import "@fontsource/play/700.css";
import "./globals.css";

import type { Metadata } from "next";
import { ColorSchemeScript, mantineHtmlProps } from "@mantine/core";

import { AppProviders } from "@/components/app/AppProviders";
import { AppShellLayout } from "@/components/app/AppShellLayout";

export const metadata: Metadata = {
  title: "Цифровой дизайнер презентаций",
  description: "Генерация презентаций в стиле шаблона с аудитом качества",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru" {...mantineHtmlProps}>
      <head>
        <ColorSchemeScript defaultColorScheme="light" />
      </head>
      <body>
        <AppProviders>
          <AppShellLayout>{children}</AppShellLayout>
        </AppProviders>
      </body>
    </html>
  );
}
