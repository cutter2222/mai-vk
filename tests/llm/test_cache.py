"""Ключ кэша, режимы off/read/read_write и record/replay, объединение одинаковых запросов."""

from __future__ import annotations

import asyncio
import json
import pathlib
from collections.abc import Callable

import pytest

from presentation_designer.llm.cache import ResponseCache, cache_key
from presentation_designer.llm.client import LlmClient
from presentation_designer.llm.stub import StubTransport
from presentation_designer.llm.types import Image, LlmError, Message, ReplayMissError, Request


def req(text: str = "привет", **kw: object) -> Request:
    base = Request(role="llm", messages=[Message("system", "s"), Message("user", text)])
    for k, v in kw.items():
        setattr(base, k, v)
    return base


def key(r: Request, **kw: str | None) -> str:
    args: dict[str, str | None] = {"provider": "p", "model": "m", "model_revision": None}
    args.update(kw)
    return cache_key(r, **args)  # type: ignore[arg-type]


def test_key_depends_on_every_input() -> None:
    base = key(req())
    assert key(req()) == base, "ключ детерминирован"
    assert key(req("другой текст")) != base
    assert key(req(), model="m2") != base
    assert key(req(), provider="p2") != base
    assert key(req(), model_revision="rev2") != base
    assert key(req(prompt=("p", "0.1.0"))) != key(req(prompt=("p", "0.2.0")))
    assert key(req(response_format="json_object")) != base
    assert key(req(schema={"type": "object"})) != key(req(schema={"type": "array"}))
    assert key(req(reasoning="off")) != key(req(reasoning="low"))
    assert key(req(temperature=0.1)) != key(req(temperature=0.2))
    assert key(req(max_output_tokens=10)) != key(req(max_output_tokens=20))
    assert key(req(regenerate_nonce="1")) != base, "явная перегенерация меняет ключ"
    img1 = Request("vlm", [Message("user", "x", (Image(b"a"),))])
    img2 = Request("vlm", [Message("user", "x", (Image(b"b"),))])
    assert key(img1) != key(img2), "содержимое изображений входит в ключ"
    # stage и variant_id — метрики, не входы модели
    assert key(req(stage="plan", variant_id="compact")) == base


async def test_read_write_then_read(make_client: Callable[..., LlmClient]) -> None:
    stub = StubTransport()
    client = make_client(stub, cache_mode="read_write")
    first = await client.complete(req())
    second = await client.complete(req())
    assert len(stub.calls) == 1, "второй запрос взят из кэша"
    assert first.cache_hit is False and second.cache_hit is True
    assert second.text == first.text and second.usage.source == "cache"
    assert client.cache.operational.count() == 1

    reader = make_client(StubTransport(), cache_mode="read")
    hit = await reader.complete(req())
    assert hit.cache_hit is True
    miss = await reader.complete(req("новое"))
    assert miss.cache_hit is False and reader.cache.operational.count() == 1, "read не пишет"

    off = make_client(StubTransport(), cache_mode="off")
    assert (await off.complete(req())).cache_hit is False


async def test_record_then_replay(
    make_client: Callable[..., LlmClient], settings: object, tmp_path: pathlib.Path
) -> None:
    stub = StubTransport()
    recorder = make_client(stub, cache_mode="record")
    resp = await recorder.complete(req("запиши"))
    assert len(stub.calls) == 1
    files = list(recorder.cache.fixtures.root.glob("*/*.json"))
    assert len(files) == 1
    entry = json.loads(files[0].read_text())
    assert entry["origin"] == "own" and entry["response"]["text"] == resp.text
    assert "images" in entry["request"]["messages"][0]

    replay_stub = StubTransport()
    player = make_client(replay_stub, cache_mode="replay")
    replayed = await player.complete(req("запиши"))
    assert replayed.text == resp.text and replayed.usage.source == "replay"
    assert replay_stub.calls == [], "replay не ходит в сеть"
    with pytest.raises(ReplayMissError, match="record"):
        await player.complete(req("этого не записывали"))
    assert replay_stub.calls == [], "промах replay тоже не ходит в сеть"


async def test_record_refuses_closed_materials(make_client: Callable[..., LlmClient]) -> None:
    client = make_client(StubTransport(), cache_mode="record")
    closed = req("текст из шаблона организаторов")
    closed.extra["origin"] = "organizer"
    with pytest.raises(LlmError, match="собственных входах"):
        await client.complete(closed)
    assert client.cache.fixtures.count() == 0


def test_unknown_mode(tmp_path: pathlib.Path) -> None:
    with pytest.raises(LlmError):
        ResponseCache("sometimes", tmp_path, tmp_path)


async def test_identical_concurrent_requests_join(make_client: Callable[..., LlmClient]) -> None:
    stub = StubTransport(latency_ms=50)
    client = make_client(stub, cache_mode="off")
    results = await asyncio.gather(*(client.complete(req("одно и то же")) for _ in range(5)))
    assert len(stub.calls) == 1, "пять одинаковых одновременных запросов — один вызов"
    assert {r.text for r in results} == {results[0].text}
    assert sum(1 for r in results if not r.cache_hit) == 1
