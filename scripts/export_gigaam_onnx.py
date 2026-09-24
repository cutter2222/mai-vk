# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "gigaam[torch] @ git+https://github.com/salute-developers/GigaAM",
#   "onnx",
#   "onnxruntime",
#   "numpy",
#   "soundfile",
#   "pyyaml",
# ]
# ///
"""Экспорт GigaAM в ONNX для сервиса распознавания речи (этап 32).

Запускается на машине разработчика, нужен интернет:

    uv run scripts/export_gigaam_onnx.py --model v3_e2e_ctc --out models/gigaam/v3_e2e_ctc

Что делает: `gigaam.load_model` → `to_onnx` (fp32) → `quantize_dynamic` (int8, MatMul) →
в каталог `--out` кладёт оба графа, `<model>.yaml` с путём к словарю относительно каталога,
`tokenizer.model`, окно и мел-фильтры из весов (`preprocessor.npz`), эталон признаков
`features_ref.npz` (извлечение torch на фикстуре и на синтетическом сигнале) и
`manifest.json`: версия GigaAM, ревизия весов, sha256 и размеры файлов, дата, какой граф
рабочий и контрольные распознавания fp32 и int8 — на фикстуре
`tests/fixtures/speech/example.wav` и на пяти кусках до 20 с длинного примера из репозитория
GigaAM. Если int8 портит контрольные фразы против fp32, рабочим остаётся fp32, и это видно в
манифесте (`graph`, `quantization.accepted`).

torch нужен только здесь: сервис считает признаки своим кодом на numpy и ставит лишь
onnxruntime и sentencepiece. Веса в git не попадают (`models/` в .gitignore), на сервер их
доставляет `make asr-model`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import shutil
import sys
import tempfile
import urllib.request
from typing import Any

import numpy as np

CDN = "https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM"
HF = {"v3_e2e_ctc": "https://huggingface.co/ai-sage/GigaAM-v3/tree/e2e_ctc"}
ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "speech" / "example.wav"
SAMPLE_RATE = 16000
LONG_PIECES = 5
PIECE_S = 20.0
# Синтетический сигнал для эталона признаков, который лежит в тестах: воспроизводится
# генератором numpy без аудиофайла и без модели.
SYNTH_SEED = 20260924
SYNTH_SECONDS = 1.5


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, target: pathlib.Path) -> pathlib.Path:
    if target.is_file() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as src, tmp.open("wb") as out:
        shutil.copyfileobj(src, out)
    tmp.replace(target)
    return target


def read_wav(path: pathlib.Path) -> np.ndarray:
    """PCM16 моно 16 кГц → float32 как `gigaam.load_audio` (int16 / 32768)."""
    import soundfile as sf

    data, rate = sf.read(str(path), dtype="int16", always_2d=True)
    if rate != SAMPLE_RATE or data.shape[1] != 1:
        raise SystemExit(
            f"{path}: нужен PCM16 моно {SAMPLE_RATE} Гц, а не {rate} Гц × {data.shape[1]}"
        )
    return data[:, 0].astype(np.float32) / 32768.0


def synth_signal() -> np.ndarray:
    """Тон со свипом, гармоники и шум: все мел-полосы получают энергию."""
    rng = np.random.default_rng(SYNTH_SEED)
    t = np.arange(int(SYNTH_SECONDS * SAMPLE_RATE)) / SAMPLE_RATE
    sweep = np.sin(2 * np.pi * (200 + 1800 * t) * t)
    tones = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 3100 * t)
    signal = 0.4 * sweep + tones + 0.05 * rng.standard_normal(t.shape)
    return (np.clip(signal, -1, 1) * 32767).astype(np.int16).astype(np.float32) / 32768.0


def features_torch(model: Any, audio: np.ndarray) -> tuple[np.ndarray, int]:
    import torch

    wav = torch.from_numpy(audio)[None, :]
    length = torch.tensor([audio.shape[0]])
    with torch.inference_mode():
        feats, feat_len = model.preprocessor(wav, length)
    return feats[0].numpy().astype(np.float32), int(feat_len[0])


def torch_text(model: Any, audio: np.ndarray) -> str:
    """Распознавание самой библиотекой (torch) — без ffmpeg: сигнал уже в памяти."""
    import torch

    wav = torch.from_numpy(audio)[None, :]
    length = torch.tensor([audio.shape[0]])
    with torch.inference_mode():
        encoded, encoded_len = model.forward(wav, length)
        return str(model._decode(encoded, encoded_len, length)[0][0])


def gigaam_revision() -> str:
    """Версия пакета и коммит, из которого uv его собрал."""
    import importlib.metadata as md

    dist = md.distribution("gigaam")
    commit = ""
    try:
        info = json.loads(dist.read_text("direct_url.json") or "{}")
        commit = str((info.get("vcs_info") or {}).get("commit_id") or "")
    except Exception:
        pass
    return f"{dist.version}+{commit[:12]}" if commit else dist.version


def words(text: str) -> list[str]:
    return [w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split() if w]


def wer(ref: str, hyp: str) -> float:
    """Доля ошибок по словам (без регистра и пунктуации) — расстояние Левенштейна."""
    r, h = words(ref), words(hyp)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hw in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw))
        prev = cur
    return prev[-1] / len(r)


def ctc_text(session: Any, feats: np.ndarray, feat_len: int, tokenizer: Any, blank: int) -> str:
    log_probs, enc_len = session.run(
        None,
        {
            session.get_inputs()[0].name: feats[None, :, :].astype(np.float32),
            session.get_inputs()[1].name: np.array([feat_len], dtype=np.int64),
        },
    )
    labels = log_probs[0].argmax(-1)[: int(enc_len[0])]
    keep = labels != blank
    keep[1:] &= labels[1:] != labels[:-1]
    return str(tokenizer.decode(labels[keep].tolist()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default="v3_e2e_ctc")
    parser.add_argument(
        "--out", type=pathlib.Path, default=ROOT / "models" / "gigaam" / "v3_e2e_ctc"
    )
    parser.add_argument(
        "--cache", type=pathlib.Path, default=pathlib.Path("~/.cache/gigaam").expanduser()
    )
    parser.add_argument(
        "--max-wer-increase",
        type=float,
        default=0.02,
        help="насколько int8 может ошибаться чаще fp32 по словам, иначе рабочий граф — fp32",
    )
    args = parser.parse_args()

    import gigaam
    import onnxruntime as ort
    import sentencepiece as spm
    import torch
    from omegaconf import OmegaConf
    from onnxruntime.quantization import QuantType, quantize_dynamic

    name = args.model
    if "ctc" not in name:
        raise SystemExit("сервис умеет только CTC-модели GigaAM")
    out: pathlib.Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    download(f"{CDN}/example.wav", FIXTURE)
    long_wav = download(f"{CDN}/long_example.wav", args.cache / "long_example.wav")

    torch.set_num_threads(max(1, torch.get_num_threads()))
    model = gigaam.load_model(name, fp16_encoder=False, device="cpu", download_root=str(args.cache))
    model.eval()
    ckpt = args.cache / f"{name}.ckpt"
    tokenizer_src = args.cache / f"{name}_tokenizer.model"

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = pathlib.Path(tmp_dir)
        model.to_onnx(dir_path=str(tmp), dtype=torch.float32)
        fp32 = out / f"{name}.fp32.onnx"
        shutil.move(tmp / f"{name}.onnx", fp32)
        cfg = OmegaConf.load(tmp / f"{name}.yaml")
    int8 = out / f"{name}.int8.onnx"
    quantize_dynamic(
        str(fp32), str(int8), op_types_to_quantize=["MatMul"], weight_type=QuantType.QInt8
    )

    # Словарь рядом с графом, путь в yaml относительный: каталог переносим на сервер как есть.
    has_spm = bool(cfg.decoding.get("model_path"))
    if has_spm:
        shutil.copyfile(tokenizer_src, out / "tokenizer.model")
        cfg.decoding.model_path = "tokenizer.model"
    OmegaConf.save(cfg, out / f"{name}.yaml")

    # Окно и мел-фильтры — буферы чекпойнта, а не заново вычисленные: в весах они округлены
    # (до 0,002 от точных), и в тихих полосах признаки расходились бы в лог-шкале на единицы.
    spectrogram = model.preprocessor.featurizer[0]
    window = spectrogram.spectrogram.window.detach().float().numpy()
    fb = spectrogram.mel_scale.fb.detach().float().numpy()
    np.savez_compressed(out / "preprocessor.npz", window=window, fb=fb)

    # Эталон признаков: torch на фикстуре и на синтетическом сигнале.
    example = read_wav(FIXTURE)
    feats, feat_len = features_torch(model, example)
    synth = synth_signal()
    synth_feats, synth_len = features_torch(model, synth)
    np.savez_compressed(
        out / "features_ref.npz",
        example_features=feats,
        example_len=feat_len,
        synth_features=synth_feats,
        synth_len=synth_len,
        synth_seed=SYNTH_SEED,
        synth_seconds=SYNTH_SECONDS,
    )
    tests_ref = ROOT / "tests" / "fixtures" / "speech" / "features_synth_ref.npz"
    tests_ref.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        tests_ref,
        features=synth_feats,
        length=synth_len,
        seed=SYNTH_SEED,
        seconds=SYNTH_SECONDS,
        window=window,
        fb=fb,
        preprocessor=json.dumps(OmegaConf.to_container(cfg.preprocessor), ensure_ascii=False),
    )

    # Контрольные распознавания: torch, ONNX fp32 и ONNX int8 на одних и тех же признаках.
    vocab = list(cfg.decoding.get("vocabulary") or [])
    if has_spm:
        sp = spm.SentencePieceProcessor(model_file=str(out / "tokenizer.model"))
        blank = sp.get_piece_size()
    else:
        sp = None
        blank = len(vocab)

    class Charwise:
        @staticmethod
        def decode(ids: list[int]) -> str:
            return "".join(vocab[i] for i in ids)

    tokenizer = sp if sp is not None else Charwise()
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    sessions = {
        "fp32": ort.InferenceSession(str(fp32), opts, providers=["CPUExecutionProvider"]),
        "int8": ort.InferenceSession(str(int8), opts, providers=["CPUExecutionProvider"]),
    }
    long_audio = read_wav(long_wav)
    # Пять равных кусков, но не длиннее 20 с (модель без нарезки принимает до 25 с).
    step = min(int(PIECE_S * SAMPLE_RATE), -(-long_audio.shape[0] // LONG_PIECES))
    pieces = [("example", example)] + [
        (f"long_{i + 1}", long_audio[i * step : (i + 1) * step])
        for i in range(LONG_PIECES)
        if i * step < long_audio.shape[0]
    ]
    controls = []
    for label, audio in pieces:
        f, n = features_torch(model, audio)
        row: dict[str, Any] = {"piece": label, "seconds": round(audio.shape[0] / SAMPLE_RATE, 2)}
        row["torch"] = torch_text(model, audio)
        for kind, session in sessions.items():
            row[kind] = ctc_text(session, f, n, tokenizer, blank)
        row["wer_int8_vs_fp32"] = round(wer(row["fp32"], row["int8"]), 4)
        controls.append(row)
        pad = " " * len(label)
        print(f"{label}: fp32 «{row['fp32']}»")
        print(f"{pad}  int8 «{row['int8']}» (WER {row['wer_int8_vs_fp32']:.3f})")
    total_words = sum(len(words(c["fp32"])) for c in controls) or 1
    int8_wer = (
        sum(wer(c["fp32"], c["int8"]) * len(words(c["fp32"])) for c in controls) / total_words
    )
    accepted = int8_wer <= args.max_wer_increase
    graph = int8 if accepted else fp32

    files = [p for p in sorted(out.iterdir()) if p.is_file() and p.name != "manifest.json"]
    manifest = {
        "model": name,
        "license": "MIT",
        "params_b": 0.22,
        "gigaam_version": gigaam_revision(),
        "weights": {
            "source": f"{CDN}/{name}.ckpt",
            "md5": gigaam._MODEL_HASHES.get(name),
            "sha256": sha256(ckpt) if ckpt.is_file() else None,
            "hf": HF.get(name),
        },
        "graph": graph.name,
        "yaml": f"{name}.yaml",
        "tokenizer": "tokenizer.model" if has_spm else None,
        "sample_rate": SAMPLE_RATE,
        "preprocessor": OmegaConf.to_container(cfg.preprocessor),
        "quantization": {
            "method": "onnxruntime.quantization.quantize_dynamic",
            "op_types": ["MatMul"],
            "weight_type": "QInt8",
            "wer_int8_vs_fp32": round(int8_wer, 4),
            "max_wer_increase": args.max_wer_increase,
            "accepted": accepted,
        },
        "controls": controls,
        "onnxruntime": ort.__version__,
        "torch": torch.__version__,
        "files": {p.name: {"sha256": sha256(p), "size_bytes": p.stat().st_size} for p in files},
        "created_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    size_mb = graph.stat().st_size / 1e6
    print(f"рабочий граф: {graph.name} ({size_mb:.0f} МБ), WER int8 против fp32 {int8_wer:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
