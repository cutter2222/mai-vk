"use client";

import { Button, Group, Modal, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";

import { api, ApiError } from "@/lib/api/client";

import { templateTitle } from "./TemplateCard";

interface Props {
  /** Шаблон к удалению; null — окно закрыто. */
  target: { template_id: string; name: string } | null;
  onClose: () => void;
  onDeleted: (templateId: string) => void;
}

/** Подтверждение удаления из библиотеки: общий для сетки и карточки шаблона. */
export function DeleteTemplateModal({ target, onClose, onDeleted }: Props) {
  const [busy, setBusy] = useState(false);
  const confirm = async () => {
    if (!target) return;
    setBusy(true);
    try {
      await api.templates.delete(target.template_id);
      onDeleted(target.template_id);
      onClose();
    } catch (e) {
      notifications.show({ color: "red", title: "Шаблон не удалён", message: e instanceof ApiError ? e.message : "Сервер недоступен" });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal opened={target !== null} onClose={onClose} title="Удалить шаблон из библиотеки?" centered>
      <Text size="sm">
        «{target ? templateTitle(target.name) : ""}» исчезнет из библиотеки вместе с профилем и миниатюрами. Проекты, которые им пользовались, останутся без шаблона; уже собранные презентации не пострадают. Тот же файл можно загрузить снова — он будет разобран заново.
      </Text>
      <Group justify="flex-end" mt="md">
        <Button variant="default" onClick={onClose}>Отмена</Button>
        <Button color="red" onClick={() => void confirm()} loading={busy} data-testid="confirm-template-delete">Удалить</Button>
      </Group>
    </Modal>
  );
}
