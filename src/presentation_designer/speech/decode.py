"""Жадное CTC-декодирование GigaAM: самый вероятный символ в каждом кадре, склейка повторов,
пропуск пустого символа (`blank_id` = размер словаря), затем словарь — sentencepiece у линий e2e
(пунктуация и нормализация) или посимвольный список из yaml у остальных."""

from __future__ import annotations

import pathlib
from collections.abc import Sequence
from typing import Any

import numpy as np


class Vocabulary:
    """Словарь модели: sentencepiece (`tokenizer.model`) или посимвольный список."""

    def __init__(self, chars: Sequence[str] = (), model_path: pathlib.Path | None = None) -> None:
        self._chars = list(chars)
        self._sp: Any = None
        if model_path is not None:
            import sentencepiece as spm

            self._sp = spm.SentencePieceProcessor(model_file=str(model_path))
        elif not self._chars:
            raise ValueError("нужен посимвольный словарь или модель sentencepiece")

    def __len__(self) -> int:
        return int(self._sp.get_piece_size()) if self._sp is not None else len(self._chars)

    @property
    def blank_id(self) -> int:
        return len(self)

    def decode(self, ids: Sequence[int]) -> str:
        if self._sp is not None:
            return str(self._sp.decode([int(i) for i in ids]))
        return "".join(self._chars[int(i)] for i in ids)


def greedy_ids(log_probs: np.ndarray, length: int | None, blank_id: int) -> list[int]:
    """Кадры (T × классы) → идентификаторы символов: argmax, повторы склеены, пустой убран.
    `length` — сколько кадров значимы (выход модели для записи без дополнения)."""
    labels = np.asarray(log_probs).argmax(axis=-1).reshape(-1)
    if length is not None:
        labels = labels[: max(0, int(length))]
    if labels.size == 0:
        return []
    keep = labels != blank_id
    keep[1:] &= labels[1:] != labels[:-1]
    return [int(x) for x in labels[keep]]


def greedy_text(log_probs: np.ndarray, length: int | None, vocab: Vocabulary) -> str:
    return " ".join(vocab.decode(greedy_ids(log_probs, length, vocab.blank_id)).split())
