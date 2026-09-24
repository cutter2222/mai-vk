"""Жадное CTC-декодирование: argmax по кадрам, склейка повторов, пустой символ — последний
класс (размер словаря), словарь sentencepiece или посимвольный."""

from __future__ import annotations

import io
import pathlib

import numpy as np
import pytest

from presentation_designer.speech.decode import Vocabulary, greedy_ids, greedy_text

TEXT = [
    "Привет, это проверка голосового ввода.",
    "Сделай заголовок на третьем слайде короче.",
    "Добавь вывод и цифры за прошлый год.",
    "Презентация для руководителей о запуске сервиса.",
] * 20


def logits(labels: list[int], classes: int) -> np.ndarray:
    """Кадры, в которых выбран заданный класс."""
    out = np.full((len(labels), classes), -5.0, dtype=np.float32)
    for t, k in enumerate(labels):
        out[t, k] = 0.0
    return out


def test_greedy_ctc_merges_repeats_and_drops_blanks() -> None:
    blank = 4
    frames = logits([blank, 1, 1, blank, 1, 2, 2, 2, blank, 3, blank], 5)
    # Повтор через пустой символ — новый символ, подряд — один.
    assert greedy_ids(frames, None, blank) == [1, 1, 2, 3]
    # Значимы только первые кадры записи.
    assert greedy_ids(frames, 5, blank) == [1, 1]
    assert greedy_ids(frames, 0, blank) == []
    assert greedy_ids(np.zeros((0, 5), dtype=np.float32), None, blank) == []


def test_charwise_vocabulary() -> None:
    vocab = Vocabulary([" ", "а", "б"])
    assert len(vocab) == 3 and vocab.blank_id == 3
    frames = logits([1, 3, 2, 2, 0, 1], 4)
    assert greedy_text(frames, None, vocab) == "аб а"
    with pytest.raises(ValueError):
        Vocabulary([])


def test_sentencepiece_vocabulary(tmp_path: pathlib.Path) -> None:
    spm = pytest.importorskip("sentencepiece")
    model = io.BytesIO()
    spm.SentencePieceTrainer.train(
        sentence_iterator=iter(TEXT), model_writer=model, vocab_size=50, character_coverage=1.0
    )
    path = tmp_path / "tokenizer.model"
    path.write_bytes(model.getvalue())
    vocab = Vocabulary(model_path=path)
    assert len(vocab) == 50 and vocab.blank_id == 50
    sp = spm.SentencePieceProcessor(model_file=str(path))
    ids = sp.encode("Сделай заголовок короче.")
    # Выход модели: токены с повторами и пустым символом между ними.
    frames: list[int] = []
    for i in ids:
        frames += [i, i, vocab.blank_id]
    assert greedy_text(logits(frames, len(vocab) + 1), None, vocab) == "Сделай заголовок короче."
