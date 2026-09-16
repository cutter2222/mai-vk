"""Кэш ответов модели и тестовые записи.

Ключ — sha256 от содержимого запроса (текст и хеши изображений), провайдера, модели и её
ревизии, версии промпта и скилла, схемы ответа и всех параметров генерации. Явная
перегенерация передаёт `regenerate_nonce` и получает новый ключ.

Два назначения с разными каталогами и режимами:
- эксплуатационный кэш `data/llm-cache`: `off`, `read`, `read_write`; в резервную копию
  не входит, сборка мусора его не трогает — это производное состояние, которое можно удалить;
- тестовые записи `tests/fixtures/llm`: `record` пишет ответы реальных вызовов на собственных
  входах, `replay` читает их и при отсутствии записи падает `ReplayMissError`, не трогая сеть.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal

from presentation_designer.llm.types import (
    JsonDict,
    LlmError,
    ReplayMissError,
    Request,
    Response,
    Usage,
)

CacheMode = Literal["off", "read", "read_write", "record", "replay"]
MODES: tuple[str, ...] = ("off", "read", "read_write", "record", "replay")
ENTRY_VERSION = 1


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cache_key(req: Request, *, provider: str, model: str, model_revision: str | None) -> str:
    """Все входы, от которых зависит ответ; stage и variant_id — метрики, в ключ не входят."""
    material: JsonDict = {
        "v": ENTRY_VERSION,
        "provider": provider,
        "model": model,
        "model_revision": model_revision,
        "role": req.role,
        "messages": [
            {
                "role": m.role,
                "text": m.text,
                "images": [
                    {
                        "sha256": hashlib.sha256(i.data).hexdigest(),
                        "mime": i.mime,
                        "detail": i.detail,
                    }
                    for i in m.images
                ],
            }
            for m in req.messages
        ],
        "response_format": req.response_format,
        "schema": req.schema,
        "schema_name": req.schema_name,
        "reasoning": req.reasoning,
        "max_output_tokens": req.max_output_tokens,
        "temperature": req.temperature,
        "seed": req.seed,
        "prompt": list(req.prompt) if req.prompt else None,
        "skill": list(req.skill) if req.skill else None,
        "extra": req.extra,
        "nonce": req.regenerate_nonce,
    }
    return hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()


def request_digest(req: Request) -> JsonDict:
    """Короткое описание запроса для записи: без изображений и без полного текста."""
    return {
        "role": req.role,
        "stage": req.stage,
        "prompt": list(req.prompt) if req.prompt else None,
        "skill": list(req.skill) if req.skill else None,
        "response_format": req.response_format,
        "messages": [
            {"role": m.role, "chars": len(m.text), "images": len(m.images)} for m in req.messages
        ],
        "preview": (req.messages[-1].text[:200] if req.messages else ""),
    }


class FileCache:
    """Каталог с записями `<xx>/<ключ>.json`; запись атомарна через временный файл."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root

    def path(self, key: str) -> pathlib.Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> JsonDict | None:
        path = self.path(key)
        if not path.exists():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return loaded if isinstance(loaded, dict) else None

    def put(self, key: str, entry: JsonDict) -> pathlib.Path:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(entry, fh, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, path)
        except BaseException:
            pathlib.Path(tmp).unlink(missing_ok=True)
            raise
        return path

    def count(self) -> int:
        return sum(1 for _ in self.root.glob("*/*.json")) if self.root.exists() else 0


def entry_from_response(req: Request, resp: Response, *, key: str, origin: str) -> JsonDict:
    return {
        "entry_version": ENTRY_VERSION,
        "key": key,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "origin": origin,
        "request": request_digest(req),
        "response": {
            "text": resp.text,
            "model": resp.model,
            "provider": resp.provider,
            "finish_reason": resp.finish_reason,
            "usage": asdict(resp.usage),
            "latency_ms": resp.latency_ms,
        },
    }


def response_from_entry(entry: JsonDict, *, source: str) -> Response:
    data = entry.get("response") or {}
    usage_raw = dict(data.get("usage") or {})
    usage_raw["source"] = source
    usage = Usage(
        prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
        completion_tokens=int(usage_raw.get("completion_tokens") or 0),
        reasoning_tokens=usage_raw.get("reasoning_tokens"),
        source=source,  # type: ignore[arg-type]
    )
    return Response(
        text=str(data.get("text", "")),
        usage=usage,
        model=str(data.get("model", "")),
        provider=str(data.get("provider", "")),
        finish_reason=data.get("finish_reason"),
        latency_ms=0,
        cache_hit=True,
        cache_key=entry.get("key"),
    )


class ResponseCache:
    """Режимы и каталоги в одном месте: клиент спрашивает `lookup` до вызова и `store` после."""

    def __init__(
        self,
        mode: str,
        cache_dir: pathlib.Path,
        fixtures_dir: pathlib.Path,
    ) -> None:
        if mode not in MODES:
            raise LlmError(f"неизвестный режим кэша {mode!r}; допустимы {', '.join(MODES)}")
        self.mode = mode
        self.operational = FileCache(cache_dir)
        self.fixtures = FileCache(fixtures_dir)

    @property
    def store_for_mode(self) -> FileCache | None:
        if self.mode in ("read", "read_write"):
            return self.operational
        if self.mode in ("record", "replay"):
            return self.fixtures
        return None

    @property
    def network_allowed(self) -> bool:
        return self.mode != "replay"

    def lookup(self, key: str) -> Response | None:
        store = self.store_for_mode
        if store is None:
            return None
        entry = store.get(key)
        if entry is None:
            if self.mode == "replay":
                raise ReplayMissError(
                    f"нет записи {key[:12]}… в {store.root}: режим replay не вызывает сеть; "
                    "запишите ответ на собственных входах в режиме record"
                )
            return None
        source = "replay" if self.mode in ("record", "replay") else "cache"
        return response_from_entry(entry, source=source)

    def store(self, key: str, req: Request, resp: Response) -> pathlib.Path | None:
        if self.mode == "read_write":
            return self.operational.put(
                key, entry_from_response(req, resp, key=key, origin="cache")
            )
        if self.mode == "record":
            origin = str(req.extra.get("origin", "own"))
            if origin != "own":
                raise LlmError(
                    "в tests/fixtures/llm записываются только ответы на собственных входах; "
                    f"origin={origin!r} не записан"
                )
            return self.fixtures.put(key, entry_from_response(req, resp, key=key, origin=origin))
        return None
