"""RQ worker whose heartbeat also restores discovery after expired Redis state."""

from __future__ import annotations

from typing import Any

from redis.client import Pipeline
from rq import Worker, worker_registration


class RegisteredWorker(Worker):
    """Keep immutable identity and queue indexes alongside every heartbeat.

    RQ's idle heartbeat only writes last_heartbeat. After VM sleep / Redis state
    loss this leaves a live process undiscoverable or registered without queues.
    Do not call register_birth again: it deletes runtime job/counter fields.
    """

    def heartbeat(self, timeout: int | None = None, pipeline: Pipeline[Any] | None = None) -> None:
        if pipeline is None:
            with self.connection.pipeline() as pending:
                self.heartbeat(timeout, pipeline=pending)
                pending.execute()
            return
        # Keep the upstream HSET/EXPIRE first: maintain_heartbeats inspects results[0].
        super().heartbeat(timeout, pipeline=pipeline)
        pipeline.hset(self.key, mapping=self.serialize())
        # Parent and work horse share this hash. Never overwrite an existing state
        # from the parent's potentially stale in-memory view while a job is running.
        pipeline.hsetnx(self.key, "state", self.get_state())
        worker_registration.register(self, pipeline=pipeline)
