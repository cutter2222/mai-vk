"""Запуск API: python -m presentation_designer.api [--host] [--port]."""

from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="presentation-designer-api")
    parser.add_argument("--host", default=os.environ.get("PD_API_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PD_API_PORT", "8000")))
    args = parser.parse_args()
    uvicorn.run(
        "presentation_designer.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_level=os.environ.get("PD_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
