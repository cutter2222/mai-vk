"""Детерминированная заглушка транспорта для тестов.

Правила: список (условие, ответ). Ответ — строка, документ (сериализуется в JSON) или
исключение (имитация 429, сети, негодного ответа). Без правил заглушка строит минимальный
документ по JSON-схеме запроса или короткий текст, зависящий только от содержимого запроса,
поэтому одинаковые запросы дают одинаковые ответы. Заглушка считает вызовы: тесты кэша
и повторов проверяют по ним, что сеть «вызывалась» нужное число раз.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.llm.tokens import estimate_request_tokens, estimate_text_tokens
from presentation_designer.llm.transport import RawResult
from presentation_designer.llm.types import JsonDict, Request, Usage

Responder = Callable[[Request, int], Any]


@dataclass
class Rule:
    match: Callable[[Request], bool]
    respond: Responder
    times: int | None = None  # сколько раз правило срабатывает; None — всегда
    hits: int = 0


@dataclass
class StubTransport:
    name: str = "stub"
    rules: list[Rule] = field(default_factory=list)
    calls: list[Request] = field(default_factory=list)
    report_usage: bool = True
    latency_ms: int = 0
    model_name: str = "stub-model"

    def on(
        self, match: Callable[[Request], bool], respond: Responder, times: int | None = None
    ) -> Rule:
        rule = Rule(match, respond, times)
        self.rules.append(rule)
        return rule

    def fail(self, exc: Exception, times: int = 1) -> Rule:
        """Первые `times` вызовов падают с exc, дальше — обычный ответ."""

        def raise_it(_req: Request, _attempt: int) -> Any:
            raise exc

        return self.on(lambda _r: True, raise_it, times)

    def answer(self, value: Any, times: int | None = None) -> Rule:
        return self.on(lambda _r: True, lambda _r, _a: value, times)

    async def complete(
        self, req: Request, *, model: str, timeout_s: float, **hints: Any
    ) -> RawResult:
        self.calls.append(req)
        attempt = len(self.calls)
        value: Any = None
        matched = False
        for rule in self.rules:
            if rule.times is not None and rule.hits >= rule.times:
                continue
            if rule.match(req):
                rule.hits += 1
                value = rule.respond(req, attempt)
                matched = True
                break
        if not matched:
            value = default_answer(req)
        if isinstance(value, BaseException):
            raise value
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)
        usage = (
            Usage(
                prompt_tokens=estimate_request_tokens(req),
                completion_tokens=estimate_text_tokens(text),
                reasoning_tokens=0,
                source="provider",
            )
            if self.report_usage
            else None
        )
        return RawResult(
            text=text,
            model=self.model_name,
            usage=usage,
            finish_reason="stop",
            latency_ms=self.latency_ms,
        )


def default_answer(req: Request) -> Any:
    digest = hashlib.sha256(
        "\n".join(f"{m.role}:{m.text}" for m in req.messages).encode("utf-8")
    ).hexdigest()[:12]
    if req.response_format == "text":
        return f"stub:{digest}"
    if req.schema is not None:
        return minimal_instance(req.schema, req.schema, digest)
    return {"stub": digest}


def minimal_instance(schema: JsonDict, root: JsonDict, seed: str, depth: int = 0) -> Any:
    """Минимальный документ по схеме: обязательные поля, первый вариант enum, пустые массивы,
    строки с отметкой заглушки. Достаточно для проверки разбора и валидации в тестах."""
    if depth > 12:
        return None
    if "$ref" in schema:
        target = _resolve_ref(schema["$ref"], root)
        return minimal_instance(target, root, seed, depth + 1)
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]
    for combinator in ("allOf", "anyOf", "oneOf"):
        if schema.get(combinator):
            merged: JsonDict = {}
            variants = schema[combinator] if combinator == "allOf" else schema[combinator][:1]
            for part in variants:
                resolved = _resolve_ref(part["$ref"], root) if "$ref" in part else part
                for key, value in resolved.items():
                    if key == "properties":
                        merged.setdefault("properties", {}).update(value)
                    elif key == "required":
                        merged["required"] = sorted(set(merged.get("required", [])) | set(value))
                    else:
                        merged.setdefault(key, value)
            rest = {k: v for k, v in schema.items() if k != combinator}
            return minimal_instance({**merged, **rest}, root, seed, depth + 1)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), kind[0])
    if kind == "object" or (kind is None and "properties" in schema):
        props: JsonDict = schema.get("properties", {})
        required = schema.get("required", list(props))
        return {
            name: minimal_instance(props.get(name, {}), root, seed, depth + 1) for name in required
        }
    if kind == "array":
        min_items = int(schema.get("minItems", 0))
        item_schema = schema.get("items", {})
        return [minimal_instance(item_schema, root, seed, depth + 1) for _ in range(min_items)]
    if kind == "string":
        fmt = schema.get("format")
        if fmt == "date-time":
            return "2026-01-01T00:00:00Z"
        if fmt == "date":
            return "2026-01-01"
        if "pattern" in schema:
            return f"stub_{seed}"[: int(schema.get("maxLength", 80))]
        text = f"заглушка {seed}"
        return text[: int(schema.get("maxLength", len(text)))]
    if kind == "integer":
        return int(schema.get("minimum", 0))
    if kind == "number":
        return float(schema.get("minimum", 0))
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    return None


def _resolve_ref(ref: str, root: JsonDict) -> JsonDict:
    if not ref.startswith("#/"):
        # Ссылки на другие файлы схем заглушка не разворачивает: они приходят уже встроенными.
        return {}
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node  # type: ignore[no-any-return]
