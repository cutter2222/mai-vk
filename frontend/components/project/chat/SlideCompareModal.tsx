"use client";

import { Button, Group, Modal, SegmentedControl, Text } from "@mantine/core";
import { useEffect, useState } from "react";

import { SlideImage } from "@/components/common/SlideImage";

export interface CompareSide {
  /** Подпись переключателя: «до · r1», «после · r2». */
  label: string;
  src: string;
}

interface Props {
  opened: boolean;
  onClose: () => void;
  title: string;
  before: CompareSide;
  after: CompareSide;
  /** С какой стороны открыть: по нажатой в карточке миниатюре. */
  initial: "before" | "after";
  /** «Показать слайд» — открыть его в ленте вариантов. */
  onShow?: () => void;
}

/**
 * Крупный просмотр правки слайда из карточки чата. Одна большая картинка и переключатель
 * «до/после» на одном месте, а не две рядом: разница на слайде видна, когда картинки
 * сменяют друг друга в той же рамке. Стрелки влево-вправо переключают стороны.
 */
export function SlideCompareModal({ opened, onClose, title, before, after, initial, onShow }: Props) {
  const [side, setSide] = useState<"before" | "after">(initial);
  // Каждое открытие начинается с той стороны, по которой нажали в карточке.
  const [prevOpened, setPrevOpened] = useState(opened);
  if (prevOpened !== opened) {
    setPrevOpened(opened);
    if (opened) setSide(initial);
  }

  useEffect(() => {
    if (!opened) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") setSide("before");
      if (e.key === "ArrowRight") setSide("after");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [opened]);

  const current = side === "before" ? before : after;
  return (
    <Modal
      opened={opened}
      onClose={onClose}
      size="min(1200px, 92vw)"
      centered
      title={<Text fw={600}>{title}</Text>}
      data-testid="compare-modal"
    >
      <Group justify="space-between" mb="sm" wrap="wrap">
        <SegmentedControl
          size="xs"
          value={side}
          onChange={(v) => setSide(v as "before" | "after")}
          data={[
            { value: "before", label: before.label },
            { value: "after", label: after.label },
          ]}
          data-testid="compare-toggle"
        />
        <Group gap="xs">
          <Text size="xs" c="dimmed">← → переключают до и после</Text>
          {onShow && (
            <Button size="xs" variant="default" onClick={() => { onShow(); onClose(); }} data-testid="compare-show">
              Показать слайд
            </Button>
          )}
        </Group>
      </Group>
      <div data-testid={`compare-${side}`}>
        <SlideImage src={current.src} alt={current.label} />
      </div>
    </Modal>
  );
}
