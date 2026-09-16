"""Учёт вызовов модели в формате `GenerationResult.metrics`.

Клиент сообщает регистратору о каждом завершённом вызове (включая попадания в кэш и ошибки);
регистратор собирает `llm_calls`, суммы токенов, ожидание квоты, повторы и попадания в кэш.
`metrics()` отдаёт фрагмент, который pipeline сливает с временем этапов.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from presentation_designer.llm.types import JsonDict, Request, Response


@dataclass
class CallRecord:
    stage: str
    model: str
    attempt: int
    variant_id: str | None = None
    slide_ids: list[str] | None = None
    prompt: tuple[str, str] | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    latency_ms: int | None = None
    cache_hit: bool = False
    ok: bool = True
    quota_wait_ms: int = 0
    error_code: str | None = None
    usage_source: str | None = None

    def as_dict(self) -> JsonDict:
        out: JsonDict = {
            "stage": self.stage,
            "model": self.model,
            "attempt": self.attempt,
            "cache_hit": self.cache_hit,
            "ok": self.ok,
        }
        if self.variant_id:
            out["variant_id"] = self.variant_id
        if self.slide_ids:
            out["slide_ids"] = list(self.slide_ids)
        if self.prompt:
            out["prompt"] = {"name": self.prompt[0], "version": self.prompt[1]}
        for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "latency_ms"):
            value = getattr(self, key)
            if value is not None:
                out[key] = int(value)
        if self.quota_wait_ms:
            out["quota_wait_ms"] = int(self.quota_wait_ms)
        if self.error_code:
            out["error_code"] = self.error_code
        return out


@dataclass
class UsageRecorder:
    """Потокобезопасный список вызовов одного задания или этапа."""

    calls: list[CallRecord] = field(default_factory=list)
    retries: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_response(self, req: Request, resp: Response) -> CallRecord:
        rec = CallRecord(
            stage=req.stage,
            model=resp.model,
            attempt=resp.attempts,
            variant_id=req.variant_id,
            slide_ids=req.slide_ids,
            prompt=req.prompt,
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
            reasoning_tokens=resp.usage.reasoning_tokens,
            latency_ms=resp.latency_ms,
            cache_hit=resp.cache_hit,
            ok=True,
            quota_wait_ms=resp.quota_wait_ms,
            usage_source=resp.usage.source,
        )
        with self._lock:
            self.calls.append(rec)
            self.retries += max(0, resp.attempts - 1)
        return rec

    def record_failure(
        self, req: Request, *, model: str, attempts: int, error_code: str, quota_wait_ms: int = 0
    ) -> CallRecord:
        rec = CallRecord(
            stage=req.stage,
            model=model,
            attempt=attempts,
            variant_id=req.variant_id,
            slide_ids=req.slide_ids,
            prompt=req.prompt,
            ok=False,
            error_code=error_code,
            quota_wait_ms=quota_wait_ms,
        )
        with self._lock:
            self.calls.append(rec)
            self.retries += max(0, attempts - 1)
        return rec

    def metrics(self) -> JsonDict:
        with self._lock:
            calls = list(self.calls)
            retries = self.retries
        hits = sum(1 for c in calls if c.cache_hit)
        misses = sum(1 for c in calls if not c.cache_hit and c.ok)
        prompt_tokens = sum(c.prompt_tokens or 0 for c in calls if not c.cache_hit)
        completion_tokens = sum(c.completion_tokens or 0 for c in calls if not c.cache_hit)
        estimated = any(c.usage_source == "estimated" for c in calls)
        out: JsonDict = {
            "llm_calls": [c.as_dict() for c in calls],
            "totals": {
                "llm_calls": len(calls),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
            "quota_wait_ms": sum(c.quota_wait_ms for c in calls),
            "retries": retries,
            "cache": {"llm_hits": hits, "llm_misses": misses},
            "cost_estimate": {"known": False},
        }
        if estimated:
            # Провайдер не вернул usage хотя бы раз: суммы токенов — консервативная оценка.
            out["totals"]["usage_estimated"] = True
        return out

    def by_stage(self) -> dict[str, JsonDict]:
        """Сводка по этапам: вызовы, токены, ожидание, задержка — для отчётов и зонда."""
        with self._lock:
            calls = list(self.calls)
        stages: dict[str, JsonDict] = {}
        for c in calls:
            s = stages.setdefault(
                c.stage,
                {
                    "calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "latency_ms": 0,
                    "quota_wait_ms": 0,
                    "cache_hits": 0,
                    "errors": 0,
                },
            )
            s["calls"] += 1
            s["prompt_tokens"] += c.prompt_tokens or 0
            s["completion_tokens"] += c.completion_tokens or 0
            s["latency_ms"] += c.latency_ms or 0
            s["quota_wait_ms"] += c.quota_wait_ms
            s["cache_hits"] += int(c.cache_hit)
            s["errors"] += int(not c.ok)
        return stages
