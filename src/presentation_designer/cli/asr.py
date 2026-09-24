"""Сервис распознавания речи для голосового ввода в чате (GigaAM, ONNX Runtime).

Запуск: python -m presentation_designer.cli.asr [--port 8010] [--model-dir …]
Параметры по умолчанию — `speech` в config/app.yaml. `--healthcheck` для Docker: сервис
отвечает на /health (модель при этом может быть не загружена — она грузится по запросу).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import signal
import sys
import threading
import urllib.request


def healthcheck(port: int) -> int:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
            body = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"healthcheck: сервис не отвечает: {e}", file=sys.stderr)
        return 1
    return 0 if body.get("status") == "ok" else 1


def main(argv: list[str] | None = None) -> int:
    from presentation_designer.shared.settings import get_settings

    cfg = get_settings().speech
    parser = argparse.ArgumentParser(
        prog="presentation-designer-asr", description=__doc__.split("\n\n")[0]
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=cfg.port)
    parser.add_argument(
        "--model-dir", type=pathlib.Path, default=get_settings().resolve(cfg.model_dir)
    )
    parser.add_argument("--idle-unload-s", type=float, default=cfg.idle_unload_s)
    parser.add_argument("--threads", type=int, default=cfg.threads)
    parser.add_argument("--healthcheck", action="store_true", help="проверить, что сервис отвечает")
    args = parser.parse_args(argv)
    if args.healthcheck:
        return healthcheck(args.port)

    logging.basicConfig(
        level=os.environ.get("PD_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    from presentation_designer.speech.server import SpeechServer
    from presentation_designer.speech.service import SpeechModel

    model = SpeechModel(args.model_dir, idle_unload_s=args.idle_unload_s, threads=args.threads)
    server = SpeechServer(
        (args.host, args.port), model, max_bytes=cfg.max_bytes, max_seconds=cfg.max_seconds
    )
    state = model.status()["state"]
    logging.getLogger(__name__).info(
        "сервис распознавания на :%d, модель %s (%s), выгрузка после %g с простоя",
        args.port,
        args.model_dir,
        state,
        args.idle_unload_s,
    )

    def stop(signum: int, frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        model.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
