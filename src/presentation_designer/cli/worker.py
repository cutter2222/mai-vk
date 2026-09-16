"""Воркер очереди: слушает заданные очереди RQ и перед стартом проверяет рендерер.

Роли задаются списком очередей: анализ и исправления — `analysis repair`,
генерация — `generation`. Запуск: python -m presentation_designer.cli.worker --queues generation
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from presentation_designer.pipeline.jobs import renderer_check, worker_name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="presentation-designer-worker", description="Воркер очереди заданий"
    )
    parser.add_argument(
        "--queues", nargs="+", default=["generation"], help="очереди RQ в порядке приоритета"
    )
    parser.add_argument("--name", default=None, help="имя воркера; по умолчанию роль и hostname")
    parser.add_argument(
        "--skip-render-check", action="store_true", help="не проверять LibreOffice при старте"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("PD_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    import redis
    from rq import Queue, Worker

    url = os.environ.get("PD_VALKEY_URL", "redis://localhost:6379/0")
    name = args.name or worker_name("-".join(args.queues)) + f"-{os.getpid()}"
    if not args.skip_render_check and "generation" in args.queues:
        ok = renderer_check(url, name)
        logging.getLogger(__name__).info("проверка рендерера: %s", "ok" if ok else "не пройдена")
    # Оркестратор строится в дочернем процессе задачи: родитель не открывает SQLite,
    # чтобы отображения памяти WAL не наследовались через fork.
    connection = redis.Redis.from_url(url)
    worker = Worker(
        [Queue(q, connection=connection) for q in args.queues], connection=connection, name=name
    )
    worker.work(with_scheduler=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
