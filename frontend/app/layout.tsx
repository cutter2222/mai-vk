import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Цифровой дизайнер презентаций",
  description: "Генерация презентаций в стиле шаблона с аудитом качества",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
