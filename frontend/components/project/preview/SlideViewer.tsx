"use client";

import { ActionIcon, Group, Text } from "@mantine/core";
import { IconChevronDown, IconChevronUp } from "@tabler/icons-react";
import { useEffect, useRef } from "react";

import { SlideImage, type Overlay } from "@/components/common/SlideImage";

export interface ViewerSlide {
  key: string;
  src?: string;
  label: string;
  /** Отметка на миниатюре: на слайде есть находки аудита. */
  flagged?: boolean;
}

interface Props {
  slides: ViewerSlide[];
  index: number;
  onIndex: (i: number) => void;
  overlays?: Overlay[];
  activeOverlay?: string | null;
  onOverlayClick?: (id: string) => void;
  /** Подпись слева от счётчика: «ревизия 2». */
  caption?: React.ReactNode;
  /** Элементы справа от счётчика. */
  actions?: React.ReactNode;
  /** Содержимое под слайдом в основной колонке: обоснование варианта, «как собран». */
  children?: React.ReactNode;
  /** Боковая панель справа от слайда: находки аудита. */
  aside?: React.ReactNode;
}

/**
 * Вертикальная лента миниатюр слева и крупный слайд справа, как в редакторах презентаций.
 * Слайды вариантов генерации; образцы шаблона показываются в его карточке в библиотеке.
 */
export function SlideViewer({ slides, index, onIndex, overlays, activeOverlay, onOverlayClick, caption, actions, children, aside }: Props) {
  const total = slides.length;
  const safeIndex = Math.min(index, Math.max(total - 1, 0));
  const current = slides[safeIndex];
  const stripRef = useRef<HTMLDivElement | null>(null);

  // Активная миниатюра держится в видимой части ленты при листании клавишами.
  useEffect(() => {
    const active = stripRef.current?.querySelector<HTMLElement>('[data-active="true"]');
    active?.scrollIntoView({ block: "nearest" });
  }, [safeIndex]);

  return (
    <div className="viewer" data-aside={Boolean(aside)}>
      {total > 0 && (
        <div className="filmstrip" ref={stripRef} data-testid="thumb-strip">
          {slides.map((s, i) => (
            <button key={s.key} type="button" data-active={i === safeIndex} onClick={() => onIndex(i)} aria-label={s.label} title={s.label}>
              <span className="thumb-number">{i + 1}</span>
              <span className="thumb-image">
                <SlideImage src={s.src} alt={s.label} />
                {s.flagged && <span className="thumb-issues" />}
              </span>
            </button>
          ))}
        </div>
      )}
      <div className="viewer-main">
        <div className="preview-stage">
          <SlideImage src={current?.src} alt={current?.label ?? "Слайд"} overlays={overlays} activeOverlay={activeOverlay} onOverlayClick={onOverlayClick} />
        </div>
        <Group justify="space-between" mt="sm" mb="xs" gap="xs" align="center">
          <Group gap="xs" style={{ flex: "1 1 auto", minWidth: 0 }}>{caption}</Group>
          <Group gap={6} wrap="nowrap" style={{ flex: "0 0 auto" }}>
            <ActionIcon variant="default" size="sm" disabled={safeIndex <= 0} onClick={() => onIndex(safeIndex - 1)} aria-label="Предыдущий слайд">
              <IconChevronUp size={14} />
            </ActionIcon>
            <Text size="sm" c="dimmed" style={{ minWidth: 92, textAlign: "center" }} data-testid="slide-counter">
              {total ? `Слайд ${safeIndex + 1} из ${total}` : "Слайдов нет"}
            </Text>
            <ActionIcon variant="default" size="sm" disabled={safeIndex >= total - 1} onClick={() => onIndex(safeIndex + 1)} aria-label="Следующий слайд">
              <IconChevronDown size={14} />
            </ActionIcon>
          </Group>
          <Group gap="xs" wrap="nowrap" style={{ flex: "0 0 auto" }}>{actions}</Group>
        </Group>
        {children}
      </div>
      {aside}
    </div>
  );
}
