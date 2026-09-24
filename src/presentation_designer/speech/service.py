"""Модель распознавания в процессе сервиса `asr`: GigaAM в ONNX Runtime на одном CPU.

Модель грузится при первом запросе и выгружается после `idle_unload_s` простоя — сервис
висит в стеке постоянно, а память (около 0,5 ГБ с моделью) занимает, только пока диктуют.
Запросы идут по одному: блокировка процесса — это и есть очередь, и одна сессия ONNX Runtime
не делит поток с соседним запросом. Каталог модели готовит `scripts/export_gigaam_onnx.py`:
`manifest.json` (какой граф рабочий), yaml экспорта, словарь и буферы признаков.
"""

from __future__ import annotations

import gc
import json
import logging
import pathlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from presentation_designer.speech.decode import Vocabulary, greedy_text
from presentation_designer.speech.features import FeatureParams, Featurizer

log = logging.getLogger(__name__)


class SpeechError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Transcript:
    text: str
    duration_ms: int
    infer_ms: int
    model: str


@dataclass
class _Loaded:
    session: Any
    featurizer: Featurizer
    vocab: Vocabulary
    model: str


class SpeechModel:
    def __init__(
        self,
        model_dir: pathlib.Path,
        *,
        idle_unload_s: float = 600.0,
        threads: int = 1,
        clock: Callable[[], float] = time.monotonic,
        watch: bool = True,
    ) -> None:
        self.model_dir = model_dir
        self.idle_unload_s = idle_unload_s
        self.threads = max(1, threads)
        self._clock = clock
        self._lock = threading.Lock()
        self._loaded: _Loaded | None = None
        self._last_used = clock()
        self.loads = 0
        self._stop = threading.Event()
        if watch and idle_unload_s > 0:
            period = max(1.0, min(30.0, idle_unload_s / 4))
            threading.Thread(
                target=self._watch, args=(period,), name="asr-idle", daemon=True
            ).start()

    # ----- состояние -----

    def manifest(self) -> dict[str, Any] | None:
        path = self.model_dir / "manifest.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        graph = self.model_dir / str(data.get("graph") or "")
        return data if graph.is_file() and graph.suffix == ".onnx" else None

    def available(self) -> bool:
        return self.manifest() is not None

    def status(self) -> dict[str, Any]:
        manifest = self.manifest()
        loaded = self._loaded
        state = "loaded" if loaded else "unloaded" if manifest else "model_missing"
        out: dict[str, Any] = {"state": state, "loads": self.loads}
        if manifest:
            out["model"] = manifest.get("model")
            out["graph"] = manifest.get("graph")
        if loaded:
            out["idle_s"] = round(self._clock() - self._last_used, 1)
        return out

    # ----- загрузка и выгрузка -----

    def _load(self) -> _Loaded:
        import onnxruntime as ort
        import yaml

        manifest = self.manifest()
        if manifest is None:
            raise SpeechError("model_missing", f"В {self.model_dir} нет модели распознавания")
        started = time.perf_counter()
        cfg = yaml.safe_load((self.model_dir / str(manifest["yaml"])).read_text(encoding="utf-8"))
        params = FeatureParams.from_config(dict(cfg.get("preprocessor") or {}))
        featurizer = Featurizer.create(params, self.model_dir / "preprocessor.npz")
        tokenizer = (cfg.get("decoding") or {}).get("model_path")
        vocab = Vocabulary(
            (cfg.get("decoding") or {}).get("vocabulary") or (),
            self.model_dir / str(tokenizer) if tokenizer else None,
        )
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.threads
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # Без арены памяти: после записи процесс возвращает буферы, а не держит пик.
        opts.enable_cpu_mem_arena = False
        opts.log_severity_level = 3
        session = ort.InferenceSession(
            str(self.model_dir / str(manifest["graph"])), opts, providers=["CPUExecutionProvider"]
        )
        self.loads += 1
        log.info(
            "модель %s загружена за %d мс (%s)",
            manifest.get("model"),
            int((time.perf_counter() - started) * 1000),
            manifest.get("graph"),
        )
        return _Loaded(session, featurizer, vocab, str(manifest.get("model") or "gigaam"))

    def unload(self) -> bool:
        with self._lock:
            if self._loaded is None:
                return False
            self._loaded = None
        gc.collect()
        _trim_heap()
        log.info("модель выгружена после простоя")
        return True

    def unload_if_idle(self) -> bool:
        """Выгрузить модель, если она простаивает дольше `idle_unload_s`."""
        if self._loaded is None or self._clock() - self._last_used < self.idle_unload_s:
            return False
        return self.unload()

    def _watch(self, period: float) -> None:
        while not self._stop.wait(period):
            self.unload_if_idle()

    def close(self) -> None:
        self._stop.set()

    # ----- распознавание -----

    def transcribe(self, audio: np.ndarray) -> Transcript:
        """float32 PCM 16 кГц в [-1, 1] → текст; запросы идут по одному."""
        with self._lock:
            if self._loaded is None:
                self._loaded = self._load()
            loaded = self._loaded
            started = time.perf_counter()
            try:
                feats = loaded.featurizer(audio)
                inputs = loaded.session.get_inputs()
                log_probs, lengths = loaded.session.run(
                    None,
                    {
                        inputs[0].name: feats[None, :, :],
                        inputs[1].name: np.array([feats.shape[1]], dtype=np.int64),
                    },
                )
                text = greedy_text(
                    log_probs[0], int(np.asarray(lengths).reshape(-1)[0]), loaded.vocab
                )
            finally:
                self._last_used = self._clock()
            infer_ms = int((time.perf_counter() - started) * 1000)
        duration_ms = round(audio.shape[0] * 1000 / loaded.featurizer.params.sample_rate)
        return Transcript(text=text, duration_ms=duration_ms, infer_ms=infer_ms, model=loaded.model)


def _trim_heap() -> None:
    """Вернуть освобождённую кучу системе (glibc): иначе процесс после выгрузки выглядит
    таким же большим, как с моделью."""
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass
