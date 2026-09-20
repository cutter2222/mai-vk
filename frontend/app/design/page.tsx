import type { Metadata } from "next";

import { DesignTokensPage } from "@/components/design/DesignTokensPage";

export const metadata: Metadata = { title: "Дизайн-система · Дизайнер презентаций" };

/**
 * Канонический визуальный контракт интерфейса: токены, типографика, компоненты и правила.
 *
 * Открывается перед тем, как добавить новый экран, компонент или цвет. Значения берутся
 * отсюда кликом — в буфер уходит имя CSS-переменной, а не hex, чтобы в разметке не
 * появлялись литералы цвета.
 */
export default function DesignPage() {
  return <DesignTokensPage />;
}
