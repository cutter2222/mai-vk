"""Воркер очереди: слушает заданные очереди RQ и перед стартом проверяет рендерер.

Роли задаются списком очередей: анализ и исправления — `analysis repair`,
генерация — `generation`. Запуск: python -m presentation_designer.cli.worker --queues generation
`--healthcheck` для Docker: воркер этого контейнера зарегистрирован в Valkey и его
отметка жива (RQ продлевает ключ воркера при каждом heartbeat).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
import uuid
from typing import Any

from presentation_designer.pipeline.jobs import renderer_check, worker_name


def healthcheck(url: str, queues: list[str] | None = None) -> int:
    """Require full identity, an expiring heartbeat and the expected queues."""
    import redis
    from rq import Worker

    host = socket.gethostname()
    try:
        connection = redis.Redis.from_url(url, socket_timeout=5, socket_connect_timeout=5)
        alive = [
            w.name
            for w in Worker.all(connection=connection)
            if w.hostname == host
            and w.pid
            and w.birth_date
            and w.last_heartbeat
            and w.queue_names()
            and (queues is None or set(queues).issubset(w.queue_names()))
            and connection.ttl(w.key) > 0
        ]
    except Exception as e:
        print(f"healthcheck: Valkey недоступен: {e}", file=sys.stderr)
        return 1
    if not alive:
        print(f"healthcheck: воркер {host} не зарегистрирован", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="presentation-designer-worker", description="Воркер очереди заданий"
    )
    parser.add_argument("--queues", nargs="+", default=None, help="очереди RQ в порядке приоритета")
    parser.add_argument("--name", default=None, help="имя воркера; по умолчанию роль и hostname")
    parser.add_argument(
        "--skip-render-check", action="store_true", help="не проверять ONLYOFFICE при старте"
    )
    parser.add_argument(
        "--healthcheck", action="store_true", help="проверить, что воркер контейнера жив"
    )
    args = parser.parse_args(argv)
    if args.healthcheck:
        return healthcheck(os.environ.get("PD_VALKEY_URL", "redis://localhost:6379/0"), args.queues)
    args.queues = args.queues or ["generation"]

    logging.basicConfig(
        level=os.environ.get("PD_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Процесс — PID 1 контейнера, а ядро не применяет к PID 1 действие по умолчанию:
    # без обработчика SIGTERM во время старта (импорты, проверка рендерера) игнорируется,
    # и docker stop ждёт всю грацию. RQ ставит свои обработчики уже в work().
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    import redis
    from rq import Queue

    from presentation_designer.pipeline.worker import RegisteredWorker
    from presentation_designer.shared.settings import get_settings

    url = os.environ.get("PD_VALKEY_URL", "redis://localhost:6379/0")
    # Суффикс имени случайный, а не PID: в контейнере PID всегда 1, и после аварийного падения
    # новый процесс упирался бы в ещё живую регистрацию прежнего (RQ отказывает на дубликате
    # имени до истечения TTL ключа, около семи минут).
    name = args.name or f"{worker_name('-'.join(args.queues))}-{uuid.uuid4().hex[:6]}"
    connection = redis.Redis.from_url(url)
    forget_stale_workers(connection, name)
    if not args.skip_render_check:
        ok = renderer_check(url, name)
        logging.getLogger(__name__).info("проверка рендерера: %s", "ok" if ok else "не пройдена")
    # Оркестратор строится в дочернем процессе задачи: родитель не открывает SQLite,
    # чтобы отображения памяти WAL не наследовались через fork.
    # Срок регистрации в Valkey — queue.heartbeat_ttl_s: после аварии или пересоздания
    # контейнера прежняя запись исчезает из /api/health за это время, а не за 7 минут RQ.
    worker = RegisteredWorker(
        [Queue(q, connection=connection) for q in args.queues],
        connection=connection,
        name=name,
        worker_ttl=get_settings().queue.heartbeat_ttl_s,
    )
    worker.work(with_scheduler=False)
    return 0


def forget_stale_workers(connection: Any, own_name: str) -> None:
    """Снимает регистрации прежних процессов этого контейнера: в контейнере один воркер,
    поэтому любая другая запись с тем же hostname — след аварийно завершённого процесса.
    Иначе до истечения TTL ключа /api/health считал бы его живым."""
    from rq import Worker

    host = socket.gethostname()
    try:
        for stale in Worker.all(connection=connection):
            if host in stale.name and stale.name != own_name:
                stale.register_death()  # type: ignore[no-untyped-call]
                connection.delete(f"pd:renderer:{stale.name}")
                logging.getLogger(__name__).info(
                    "снята регистрация прежнего воркера %s", stale.name
                )
    except Exception:
        logging.getLogger(__name__).exception("не удалось снять устаревшие регистрации воркеров")


if __name__ == "__main__":
    sys.exit(main())
