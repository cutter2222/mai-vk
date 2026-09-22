from __future__ import annotations

import os
import socket
import uuid
from unittest.mock import MagicMock, patch

import pytest
from redis import Redis
from rq import Worker
from rq.utils import now

from presentation_designer.cli.worker import healthcheck
from presentation_designer.pipeline.worker import RegisteredWorker


def test_heartbeat_retains_upstream_command_order_and_restores_identity() -> None:
    connection = MagicMock()
    connection.connection_pool.connection_kwargs = {}
    worker = RegisteredWorker(["sandbox"], connection=connection, prepare_for_work=False)
    worker.birth_date = now()
    worker.hostname = "test-host"
    worker.pid = 123
    worker._state = "idle"
    pipeline = MagicMock()
    worker.heartbeat(90, pipeline=pipeline)
    calls = pipeline.method_calls
    assert calls[0].args[:2] == (worker.key, "last_heartbeat")
    assert calls[1].args == (worker.key, 90)
    assert calls[2].kwargs["mapping"]["queues"] == "sandbox"
    assert calls[2].kwargs["mapping"]["hostname"] == "test-host"
    pipeline.hsetnx.assert_called_once_with(worker.key, "state", "idle")
    assert pipeline.sadd.call_count == 2
    pipeline.execute.assert_not_called()  # The caller owns this transaction.


def test_standalone_heartbeat_uses_one_transaction() -> None:
    connection = MagicMock()
    connection.connection_pool.connection_kwargs = {}
    worker = RegisteredWorker(["sandbox"], connection=connection, prepare_for_work=False)
    worker.birth_date = now()
    worker.heartbeat()
    connection.pipeline.return_value.__enter__.return_value.execute.assert_called_once()


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({}, 0),
        ({"hostname": "other-host"}, 1),
        ({"queues": []}, 1),
        ({"queues": ["analysis"]}, 1),
        ({"pid": None}, 1),
        ({"birth_date": None}, 1),
        ({"last_heartbeat": None}, 1),
        ({"ttl": -1}, 1),
        ({"ttl": -2}, 1),
    ],
)
def test_healthcheck_rejects_partial_registration(change: dict, expected: int) -> None:
    state = dict(hostname=socket.gethostname(), pid=12, birth_date=now(), last_heartbeat=now())
    state.update({k: v for k, v in change.items() if k not in {"queues", "ttl"}})
    worker = MagicMock(**state)
    worker.queue_names.return_value = change.get("queues", ["analysis", "repair"])
    with patch("redis.Redis.from_url") as connect, patch("rq.Worker.all", return_value=[worker]):
        connect.return_value.ttl.return_value = change.get("ttl", 90)
        assert healthcheck("redis://unused", ["analysis", "repair"]) == expected


def test_healthcheck_fails_closed_on_connection_error() -> None:
    with patch("redis.Redis.from_url", side_effect=OSError("offline")):
        assert healthcheck("redis://unused") == 1


@pytest.fixture
def registered_worker():
    url = os.environ.get("PD_TEST_VALKEY_URL")
    if not url:
        pytest.skip("Requires an explicitly supplied disposable Valkey")
    connection = Redis.from_url(url, socket_timeout=5)
    name = f"registration-test-{uuid.uuid4().hex}"
    worker = RegisteredWorker([name], name=name, connection=connection, worker_ttl=90)
    worker.register_birth()
    worker.set_state("idle")
    try:
        yield worker
    finally:
        worker.register_death()
        connection.delete(worker.key)
        connection.close()


@pytest.mark.parametrize("loss", ["expired", "heartbeat-only", "indexes", "all"])
def test_recovers_registration_and_queue_discovery(registered_worker, loss: str) -> None:
    worker = registered_worker
    connection = worker.connection
    birth = connection.hget(worker.key, "birth")
    if loss in {"expired", "heartbeat-only", "all"}:
        connection.delete(worker.key)
    if loss == "heartbeat-only":
        connection.hset(worker.key, "last_heartbeat", "stale")
    if loss in {"indexes", "all"}:
        connection.srem(worker.redis_workers_keys, worker.key)
        connection.srem(f"rq:workers:{worker.queue_names()[0]}", worker.key)
    worker.heartbeat()
    restored = Worker.find_by_key(worker.key, connection=connection)
    assert restored.hostname == socket.gethostname()
    assert restored.queue_names() == worker.queue_names()
    assert restored.get_state() == "idle"
    assert connection.hget(worker.key, "birth") == birth
    assert 0 < connection.ttl(worker.key) <= 150
    assert worker.name in [w.name for w in Worker.all(connection=connection)]
    assert worker.name in [w.name for w in Worker.all(queue=worker.queues[0])]


def test_heartbeat_preserves_runtime_fields_in_pipeline(registered_worker) -> None:
    worker = registered_worker
    connection = worker.connection
    connection.hset(
        worker.key, mapping={"state": "busy", "current_job": "job-1", "successful_job_count": 7}
    )
    with connection.pipeline() as pipeline:
        worker.heartbeat(80, pipeline=pipeline)
        pipeline.execute()
    assert connection.hget(worker.key, "state") == b"busy"
    assert connection.hget(worker.key, "current_job") == b"job-1"
    assert connection.hget(worker.key, "successful_job_count") == b"7"
    assert 0 < connection.ttl(worker.key) <= 80
