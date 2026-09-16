"""Зонд провайдера моделей и служебные проверки адаптера.

    python -m presentation_designer.llm.probe probe [--report docs/llm-capabilities.md]
                                                    [--concurrency 1,2,4] [--role llm]
    python -m presentation_designer.llm.probe smoke        # один реальный вызов (контейнер воркера)
    python -m presentation_designer.llm.probe list-models  # каталог моделей провайдера
    python -m presentation_designer.llm.probe limiter --processes 4 --requests 6 --concurrency 2

Зонд измеряет то, от чего зависят промпты следующих этапов: точный model ID, JSON-схема и
JSON-режим, одно и несколько изображений, usage, streaming (время первого токена), параметры
рассуждения, задержки при одновременности 1 → 2 → 4. Задачи зонда — собственные маленькие
(план из трёх разделов, извлечение брифа из фразы, чтение числа с картинки), бизнес-промпты
этапов 5–10 здесь не пишутся. Ключи и адреса с токенами в отчёт не попадают: только хост.
Короткий зонд не доказывает RPM/TPM — документированные квоты записываются отдельно.

Модуль лежит в пакете, а не в scripts/, потому что scripts/ не входит в образы: smoke-вызов
из контейнера воркера — часть приёмки этапа. `scripts/probe_llm.py` — тонкая обёртка.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from presentation_designer.llm.client import (
    LlmClient,
    Target,
    build_client,
    describe_provider,
    quota_for_target,
    redact_url,
)
from presentation_designer.llm.limiter import Quota, ValkeyLimiter
from presentation_designer.llm.transport import OpenAITransport, reasoning_content_of
from presentation_designer.llm.types import (
    Deadline,
    Image,
    JsonDict,
    LlmError,
    Message,
    ProviderError,
    Request,
)
from presentation_designer.llm.usage import UsageRecorder
from presentation_designer.shared.settings import ROOT, Settings, get_models_config, get_settings

PROBE_VERSION = "0.1.0"


# ---------- собственные маленькие задачи зонда (не бизнес-промпты этапов 5–10) ----------


class ProbeSection(BaseModel):
    title: str
    key_points: list[str] = Field(min_length=1, max_length=4)


class ProbePlan(BaseModel):
    title: str
    sections: list[ProbeSection] = Field(min_length=3, max_length=3)


class ProbeBrief(BaseModel):
    topic: str
    audience: str
    goal: str
    slide_count_min: int | None = None
    slide_count_max: int | None = None


class ProbeImageReading(BaseModel):
    number: int
    title: str


class ProbeWhichImage(BaseModel):
    index: int = Field(ge=1, le=2)
    numbers: list[int] = Field(min_length=2, max_length=2)


PLAN_SYSTEM = (
    "Ты помогаешь составить план короткой презентации. Отвечай только JSON по схеме: "
    "title — название, sections — ровно три раздела, у каждого title и key_points (1–4 коротких "
    "тезиса). Русский язык, без пояснений."
)
PLAN_USER = (
    "Тема: запуск сервиса умных уведомлений в компании. Аудитория: руководители подразделений. "
    "Цель: одобрить расширение пилота."
)
BRIEF_SYSTEM = (
    "Извлеки из фразы пользователя параметры презентации. Ответ — только JSON по схеме: "
    "topic, audience, goal, slide_count_min, slide_count_max (числа или null)."
)
BRIEF_USER = (
    "Сделай презентацию на 10–12 слайдов про итоги квартала для совета директоров, "
    "чтобы утвердили бюджет следующего квартала"
)
IMAGE_SYSTEM = (
    "На изображении слайд с заголовком и одним крупным числом. Ответ — только JSON по схеме: "
    "number (целое число со слайда), title (текст заголовка)."
)
MULTI_SYSTEM = (
    "Даны два изображения слайдов, на каждом одно крупное число. Ответ — только JSON по схеме: "
    "index — номер изображения (1 или 2) с большим числом, numbers — оба числа по порядку."
)


def render_slide_image(title: str, number: int, color: tuple[int, int, int]) -> Image:
    """Собственная картинка для зонда VLM: заголовок и крупное число на цветной плашке."""
    from PIL import Image as PilImage
    from PIL import ImageDraw, ImageFont

    canvas = PilImage.new("RGB", (640, 360), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    font_title: Any
    font_number: Any
    try:
        font_title = ImageFont.truetype("DejaVuSans.ttf", 30)
        font_number = ImageFont.truetype("DejaVuSans-Bold.ttf", 110)
    except OSError:
        font_title = ImageFont.load_default(30)
        font_number = ImageFont.load_default(110)
    draw.text((32, 28), title, fill=(30, 30, 30), font=font_title)
    draw.rectangle((32, 100, 608, 320), fill=color)
    draw.text((80, 130), str(number), fill=(255, 255, 255), font=font_number)
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return Image(buf.getvalue(), "image/png")


# ---------- результаты ----------


@dataclass
class Check:
    name: str
    ok: bool | None
    detail: str = ""
    latency_ms: int | None = None
    usage: JsonDict | None = None
    extra: JsonDict = field(default_factory=dict)
    # info — сведение о провайдере (кэш, принимаемые параметры): «нет» здесь не провал зонда.
    kind: str = "check"


@dataclass
class ProbeResult:
    started_at: str
    provider: JsonDict
    roles: dict[str, str]
    checks: list[Check] = field(default_factory=list)
    concurrency: list[JsonDict] = field(default_factory=list)
    documented_limits: JsonDict = field(default_factory=dict)
    models_catalog: list[str] = field(default_factory=list)
    finished_at: str | None = None
    notes: list[str] = field(default_factory=list)

    def check(self, name: str) -> Check | None:
        return next((c for c in self.checks if c.name == name), None)

    def as_dict(self) -> JsonDict:
        data = asdict(self)
        return data


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _usage_dict(resp: Any) -> JsonDict | None:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "source": usage.source,
    }


def _reasoning_of(resp: Any) -> str | None:
    raw = getattr(resp, "raw", None) or {}
    choices = raw.get("choices") or [{}]
    return reasoning_content_of(choices[0].get("message"))


def _err(e: BaseException) -> str:
    text = str(e)
    return f"{type(e).__name__}: {text[:300]}"


# ---------- зонд ----------


class Probe:
    def __init__(
        self,
        client: LlmClient,
        *,
        role: str,
        vlm_role: str,
        deadline_s: float,
        documented: JsonDict,
    ) -> None:
        self.client = client
        self.role = role
        self.vlm_role = vlm_role
        self.deadline_s = deadline_s
        self.target: Target = client.target(role)
        self.vlm_target: Target = client.target(vlm_role)
        self.result = ProbeResult(
            started_at=_now(),
            provider=describe_provider(client.models),
            roles={role: self.target.model, vlm_role: self.vlm_target.model},
            documented_limits=documented,
        )

    def _req(
        self,
        system: str,
        user: str,
        *,
        role: str | None = None,
        fmt: str = "json_schema",
        schema: type[BaseModel] | None = None,
        images: tuple[Image, ...] = (),
        reasoning: str | None = "off",
        max_output_tokens: int = 600,
        stage: str = "probe",
        unique: bool = True,
    ) -> Request:
        # Уникальная метка в тексте: у провайдера может быть свой кэш по содержимому запроса,
        # и без метки повторный зонд измерял бы его, а не инференс.
        if unique:
            user = f"{user}\n\n[зонд {uuid.uuid4().hex[:10]}]"
        return Request(
            role=role or self.role,
            messages=[Message("system", system), Message("user", user, images)],
            response_format=fmt,  # type: ignore[arg-type]
            schema=schema.model_json_schema() if schema and fmt == "json_schema" else None,
            schema_name=schema.__name__ if schema else "response",
            reasoning=reasoning,  # type: ignore[arg-type]
            max_output_tokens=max_output_tokens,
            temperature=0.0,
            stage=stage,
            deadline=Deadline.after(self.deadline_s),
            prompt=("probe", PROBE_VERSION),
        )

    async def run_check(
        self, name: str, req: Request, *, parse: type[BaseModel] | None = None, verify: Any = None
    ) -> Check:
        started = time.monotonic()
        try:
            resp = await self.client.complete(req, parse=parse)
        except LlmError as e:
            check = Check(
                name,
                False,
                _err(e),
                int((time.monotonic() - started) * 1000),
                extra={"code": e.code},
            )
            self.result.checks.append(check)
            return check
        detail = f"finish={resp.finish_reason}, попыток {resp.attempts}"
        reasoning = _reasoning_of(resp)
        detail += (
            f"; рассуждение ~{resp.usage.reasoning_tokens} токенов"
            if reasoning
            else "; без рассуждения"
        )
        ok: bool | None = True
        if verify is not None:
            try:
                verdict = verify(resp)
                if verdict is not True:
                    ok = False
                    detail += f"; проверка: {verdict}"
            except Exception as e:  # проверка — не сеть, показываем причину
                ok = False
                detail += f"; проверка упала: {_err(e)}"
        check = Check(
            name,
            ok,
            detail,
            resp.latency_ms,
            _usage_dict(resp),
            extra={
                "model": resp.model,
                "text_chars": len(resp.text),
                "reasoning_chars": len(reasoning or ""),
            },
        )
        self.result.checks.append(check)
        return check

    async def catalog(self) -> None:
        transport = self.client.transport_for(self.target)
        if not isinstance(transport, OpenAITransport):
            self.result.notes.append("каталог моделей: транспорт не OpenAI, пропущено")
            return
        try:
            ids = await transport.list_models()
        except LlmError as e:
            self.result.checks.append(Check("models_list", None, f"GET /models: {_err(e)}"))
            return
        self.result.models_catalog = ids
        found = {m: (m in ids) for m in set(self.result.roles.values())}
        self.result.checks.append(
            Check(
                "models_list",
                all(found.values()) if ids else None,
                f"в каталоге {len(ids)} моделей; настроенные: "
                + ", ".join(f"{m} — {'есть' if ok else 'нет'}" for m, ok in found.items()),
            )
        )

    async def basics(self) -> None:
        # 1. Обычный текст: жив ли endpoint, приходит ли usage.
        # Бюджет 200: у провайдеров с рассуждением он делится между рассуждением и ответом.
        req = self._req(
            "Отвечай одним словом.", "Столица Франции?", fmt="text", max_output_tokens=200
        )
        check = await self.run_check(
            "text", req, verify=lambda r: True if "париж" in r.text.lower() else f"ответ {r.text!r}"
        )
        self.result.checks.append(
            Check(
                "usage",
                (check.usage or {}).get("source") == "provider" if check.ok is not None else None,
                "usage в ответе провайдера"
                if (check.usage or {}).get("source") == "provider"
                else "usage не пришёл: лимитер считает по оценке",
            )
        )
        # 1a. Кэш на стороне провайдера: тот же текст дважды — второй ответ быстрее и совпадает?
        same = self._req(
            "Ответь одним предложением.",
            "Назови три преимущества короткой презентации.",
            fmt="text",
            max_output_tokens=200,
            unique=True,
        )
        first = await self.run_check("provider_cache_first", same)
        again = Request(**{**same.__dict__, "regenerate_nonce": "second"})
        second = await self.run_check("provider_cache_second", again)
        if first.ok is not None and second.ok is not None:
            cached = (
                second.latency_ms is not None
                and first.latency_ms is not None
                and first.latency_ms > 0
                and second.latency_ms < first.latency_ms / 3
                and second.extra.get("text_chars") == first.extra.get("text_chars")
            )
            self.result.checks.append(
                Check(
                    "provider_cache",
                    cached,
                    "повтор того же текста вернулся быстрее в 3+ раза с тем же ответом: "
                    "у провайдера кэш по содержимому, замеры свежей генерации требуют "
                    "уникальных входов"
                    if cached
                    else "повтор того же текста считался заново",
                    kind="info",
                )
            )
        # 2. json_object без схемы.
        await self.run_check(
            "json_object",
            self._req(BRIEF_SYSTEM, BRIEF_USER, fmt="json_object", max_output_tokens=300),
            parse=ProbeBrief,
            verify=lambda r: (
                True
                if isinstance(r.parsed, ProbeBrief) and r.parsed.slide_count_min == 10
                else f"разбор {r.parsed!r}"
            ),
        )
        # 3. json_schema строгая схема на задаче планирования.
        await self.run_check(
            "json_schema",
            self._req(PLAN_SYSTEM, PLAN_USER, fmt="json_schema", schema=ProbePlan),
            parse=ProbePlan,
            verify=lambda r: (
                True
                if isinstance(r.parsed, ProbePlan) and len(r.parsed.sections) == 3
                else f"разбор {r.parsed!r}"
            ),
        )
        # 4. Извлечение брифа по схеме.
        await self.run_check(
            "brief_extract",
            self._req(BRIEF_SYSTEM, BRIEF_USER, fmt="json_schema", schema=ProbeBrief),
            parse=ProbeBrief,
            verify=lambda r: (
                True
                if isinstance(r.parsed, ProbeBrief)
                and r.parsed.slide_count_min == 10
                and r.parsed.slide_count_max == 12
                else f"разбор {r.parsed!r}"
            ),
        )

    async def images(self) -> None:
        one = render_slide_image("Итоги квартала", 42, (46, 90, 200))
        two = render_slide_image("Динамика продаж", 17, (200, 80, 60))
        await self.run_check(
            "image",
            self._req(
                IMAGE_SYSTEM,
                "Прочитай число и заголовок со слайда.",
                role=self.vlm_role,
                fmt="json_schema",
                schema=ProbeImageReading,
                images=(one,),
                max_output_tokens=200,
            ),
            parse=ProbeImageReading,
            verify=lambda r: (
                True
                if isinstance(r.parsed, ProbeImageReading) and r.parsed.number == 42
                else f"разбор {r.parsed!r}"
            ),
        )
        await self.run_check(
            "multi_image",
            self._req(
                MULTI_SYSTEM,
                "Первое изображение — слайд 1, второе — слайд 2.",
                role=self.vlm_role,
                fmt="json_schema",
                schema=ProbeWhichImage,
                images=(one, two),
                max_output_tokens=200,
            ),
            parse=ProbeWhichImage,
            verify=lambda r: (
                True
                if isinstance(r.parsed, ProbeWhichImage)
                and r.parsed.index == 1
                and r.parsed.numbers == [42, 17]
                else f"разбор {r.parsed!r}"
            ),
        )

    async def reasoning(self) -> None:
        """Режимы off и low через reasoning_style провайдера: off должен убрать рассуждение
        из ответа, low — его допускает; сравниваются задержка и токены."""
        for mode in ("off", "low"):
            req = self._req(
                PLAN_SYSTEM,
                f"{PLAN_USER} Режим {mode}.",
                fmt="json_schema",
                schema=ProbePlan,
                reasoning=mode,
                max_output_tokens=1500,
            )
            verify = (
                (lambda r: True if not _reasoning_of(r) else "рассуждение не выключилось")
                if mode == "off"
                else None
            )
            check = await self.run_check(f"reasoning_{mode}", req, parse=ProbePlan, verify=verify)
            check.detail += f"; стиль {self.target.provider.reasoning_style}"
        # Какие параметры рассуждения endpoint вообще принимает: полезно при переходе на VK.
        for label, extra in (
            ("param_reasoning_effort", {"reasoning_effort": "low"}),
            ("param_enable_thinking", {"enable_thinking": False}),
            ("param_chat_template_kwargs", {"chat_template_kwargs": {"enable_thinking": False}}),
        ):
            req = self._req(
                "Отвечай одним словом.",
                "Столица Италии?",
                fmt="text",
                reasoning="provider_default",
                max_output_tokens=200,
            )
            req.extra["extra_body"] = extra
            check = await self.run_check(
                label,
                req,
                verify=lambda r: True if "рим" in r.text.lower() else f"ответ {r.text!r}",
            )
            check.kind = "info"
            if check.ok is False and check.extra.get("code") == "provider_rejected":
                check.detail = "endpoint отклоняет параметр (HTTP 400)"
            elif check.ok:
                reasoned = bool(check.extra.get("reasoning_chars"))
                tail = "рассуждение осталось" if reasoned else "рассуждения нет"
                check.detail = f"принят; {tail}"

    async def streaming(self) -> None:
        transport = self.client.transport_for(self.target)
        if not isinstance(transport, OpenAITransport):
            self.result.checks.append(Check("streaming", None, "транспорт не OpenAI"))
            return
        req = self._req(
            "Отвечай кратко, 2–3 предложения.",
            "Зачем презентации нужен смысловой план?",
            fmt="text",
            max_output_tokens=200,
        )
        try:
            raw = await transport.stream_probe(
                req, model=self.target.model, timeout_s=self.client.settings.timeouts.llm_call_s
            )
        except LlmError as e:
            self.result.checks.append(Check("streaming", False, _err(e)))
            return
        self.result.checks.append(
            Check(
                "streaming",
                bool(raw.text),
                f"первый токен через {raw.ttft_ms} мс, usage в потоке: "
                f"{'есть' if raw.usage else 'нет'}",
                raw.latency_ms,
                asdict(raw.usage) if raw.usage else None,
                extra={"ttft_ms": raw.ttft_ms},
            )
        )

    async def concurrency(self, levels: list[int]) -> None:
        """Одновременность растёт от 1 в пределах известной квоты; 4 и 8 — кандидаты."""
        quota = quota_for_target(self.target, self.client.settings)
        for level in levels:
            if level > quota.concurrency:
                self.result.notes.append(
                    f"одновременность {level} выше квоты лимитера {quota.concurrency}: пропущена"
                )
                continue
            reqs = []
            for i in range(level):
                req = self._req(
                    PLAN_SYSTEM,
                    f"{PLAN_USER} Вариант подачи №{i + 1}.",
                    fmt="json_schema",
                    schema=ProbePlan,
                )
                # Разные nonce: запросы не объединяются и не берутся из кэша — это замер сети.
                req.regenerate_nonce = f"{level}-{i}"
                reqs.append(req)
            started = time.monotonic()
            outcomes = await asyncio.gather(
                *(self.client.complete(r, parse=ProbePlan) for r in reqs), return_exceptions=True
            )
            wall_ms = int((time.monotonic() - started) * 1000)
            latencies = [o.latency_ms for o in outcomes if not isinstance(o, BaseException)]
            errors = [o for o in outcomes if isinstance(o, BaseException)]
            rate_limited = sum(
                1 for e in errors if isinstance(e, ProviderError) and e.status == 429
            )
            tokens = sum(o.usage.total for o in outcomes if not isinstance(o, BaseException))
            self.result.concurrency.append(
                {
                    "level": level,
                    "requests": level,
                    "ok": len(latencies),
                    "errors": len(errors),
                    "rate_limited": rate_limited,
                    "wall_ms": wall_ms,
                    "p50_ms": int(statistics.median(latencies)) if latencies else None,
                    "max_ms": max(latencies) if latencies else None,
                    "quota_wait_ms": sum(
                        o.quota_wait_ms for o in outcomes if not isinstance(o, BaseException)
                    ),
                    "tokens": tokens,
                    "throughput_rpm": round(len(latencies) / (wall_ms / 60000), 1)
                    if wall_ms and latencies
                    else None,
                    "error_samples": [_err(e) for e in errors[:2]],
                }
            )
            if rate_limited:
                self.result.notes.append(
                    f"на одновременности {level} получены 429: выше не поднимаемся"
                )
                break

    async def run(self, levels: list[int], *, skip_images: bool = False) -> ProbeResult:
        await self.catalog()
        await self.basics()
        if not skip_images:
            await self.images()
        await self.reasoning()
        await self.streaming()
        await self.concurrency(levels)
        self.result.finished_at = _now()
        return self.result


# ---------- отчёт ----------


def _mark(ok: bool | None, kind: str = "check") -> str:
    if ok is None:
        return "не проверено"
    if kind == "info":
        return "есть" if ok else "нет"
    return "да" if ok else "нет"


def failed_checks(result: ProbeResult) -> list[str]:
    return [c.name for c in result.checks if c.ok is False and c.kind != "info"]


def render_report(result: ProbeResult, *, commit: str | None) -> str:
    p = result.provider
    lines: list[str] = []
    lines.append("# Возможности провайдера моделей")
    lines.append("")
    lines.append(
        f"Зонд `presentation_designer.llm.probe` {PROBE_VERSION}, запуск {result.started_at}"
        + (f", commit `{commit}`" if commit else "")
        + f". Провайдер `{p['name']}` ({p['kind']}), хост `{p.get('host') or '—'}`; ключи и "
        "полные адреса в отчёт не входят."
    )
    lines.append("")
    lines.append("## Роли и model ID")
    lines.append("")
    lines.append("| Роль | Model ID | В каталоге /models |")
    lines.append("| --- | --- | --- |")
    for role, model in result.roles.items():
        in_catalog = (
            _mark(model in result.models_catalog) if result.models_catalog else "каталог недоступен"
        )
        lines.append(f"| {role} | `{model}` | {in_catalog} |")
    lines.append("")
    lines.append("## Проверки")
    lines.append("")
    lines.append(
        "| Проверка | Итог | Задержка, мс | Токены (вход/выход/рассуждение) | Подробности |"
    )
    lines.append("| --- | --- | --- | --- | --- |")
    for c in result.checks:
        u = c.usage or {}
        tokens = (
            f"{u.get('prompt_tokens', '—')}/{u.get('completion_tokens', '—')}/"
            f"{u.get('reasoning_tokens', '—')}"
            if u
            else "—"
        )
        lines.append(
            f"| {c.name} | {_mark(c.ok, c.kind)} | "
            f"{c.latency_ms if c.latency_ms is not None else '—'} | "
            f"{tokens} | {c.detail.replace('|', '/')} |"
        )
    lines.append("")
    lines.append("## Одновременность")
    lines.append("")
    if result.concurrency:
        lines.append(
            "| Уровень | Успешно | Ошибок (429) | Общее время, мс | p50, мс | max, мс | "
            "Ожидание квоты, мс | Токены | Наблюдаемый RPM |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for row in result.concurrency:
            lines.append(
                f"| {row['level']} | {row['ok']}/{row['requests']} | {row['errors']} "
                f"({row['rate_limited']}) | {row['wall_ms']} | {row['p50_ms'] or '—'} | "
                f"{row['max_ms'] or '—'} | {row['quota_wait_ms']} | {row['tokens']} | "
                f"{row['throughput_rpm'] or '—'} |"
            )
    else:
        lines.append("Замер не выполнялся.")
    lines.append("")
    lines.append("## Квоты")
    lines.append("")
    doc = result.documented_limits
    lines.append(
        "Документированные квоты провайдера (из настроек или аргументов зонда): "
        f"одновременность {doc.get('concurrency') or '—'}, RPM {doc.get('rpm') or '—'}, "
        f"TPM {doc.get('tpm') or '—'}. Короткий зонд их не доказывает: наблюдаемая пропускная "
        "способность выше — это несколько запросов за секунды, а не минута под нагрузкой. "
        "В `config/models.yaml` записываются документированные значения, лимитер работает по ним."
    )
    lines.append("")
    if result.notes:
        lines.append("## Замечания")
        lines.append("")
        lines.extend(f"- {n}" for n in result.notes)
        lines.append("")
    lines.append("## Фрагмент для config/models.yaml")
    lines.append("")
    lines.append("```yaml")
    lines.append(yaml_snippet(result))
    lines.append("```")
    lines.append("")
    lines.append(
        f"Завершено {result.finished_at or '—'}. Полный JSON — в `runs/probe/` (не коммитится)."
    )
    return "\n".join(lines) + "\n"


def yaml_snippet(result: ProbeResult) -> str:
    def flag(name: str) -> str:
        c = result.check(name)
        return "null" if c is None or c.ok is None else ("true" if c.ok else "false")

    date = result.started_at[:10]
    roles = "\n".join(
        f"  {role}:\n    model: {model}\n    verified: {{ by: probe, date: '{date}', "
        f"source: 'docs/llm-capabilities.md' }}"
        for role, model in result.roles.items()
    )
    return (
        f"providers:\n  {result.provider['name']}:\n    supports:\n"
        f"      json_schema: {flag('json_schema')}\n      json_object: {flag('json_object')}\n"
        f"      images: {flag('image')}\n      multi_image: {flag('multi_image')}\n"
        f"      usage: {flag('usage')}\n"
        f"    limits:  # документированные квоты, не результат зонда\n"
        f"      concurrency: {result.documented_limits.get('concurrency') or 'null'}\n"
        f"      rpm: {result.documented_limits.get('rpm') or 'null'}\n"
        f"      tpm: {result.documented_limits.get('tpm') or 'null'}\nroles:\n{roles}"
    )


# ---------- команды ----------


def _client_for_probe(settings: Settings, cache_mode: str) -> LlmClient:
    models = get_models_config()
    return build_client(settings, models, cache_mode=cache_mode, recorder=UsageRecorder())


def _require_configured(client: LlmClient, role: str) -> Target:
    target = client.target(role)
    if not target.provider.configured():
        p = target.provider
        raise SystemExit(
            f"провайдер {target.provider_name} не настроен: задайте {p.env_base_url} и "
            f"{p.env_api_key} (.env локально, /srv/…/.env на сервере). Зонд не выполняется."
        )
    return target


async def cmd_probe(args: argparse.Namespace) -> int:
    settings = get_settings()
    client = _client_for_probe(settings, "off")
    target = _require_configured(client, args.role)
    documented = {
        "concurrency": args.documented_concurrency or target.provider.limits.concurrency,
        "rpm": args.documented_rpm or target.provider.limits.rpm,
        "tpm": args.documented_tpm or target.provider.limits.tpm,
    }
    probe = Probe(
        client,
        role=args.role,
        vlm_role=args.vlm_role,
        deadline_s=args.deadline_s,
        documented=documented,
    )
    levels = [int(x) for x in args.concurrency.split(",") if x.strip()]
    try:
        result = await probe.run(levels, skip_images=args.skip_images)
    finally:
        await client.aclose()
    runs_dir = settings.runs_dir / "probe"
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = result.started_at.replace(":", "").replace("-", "")
    json_path = runs_dir / f"llm-{stamp}.json"
    json_path.write_text(json.dumps(result.as_dict(), ensure_ascii=False, indent=1))
    report = render_report(result, commit=os.environ.get("PD_BUILD_COMMIT") or _git_commit())
    if args.report:
        path = pathlib.Path(args.report)
        path.write_text(report, encoding="utf-8")
        print(f"отчёт: {path}", file=sys.stderr)
    else:
        print(report)
    print(f"json: {json_path}", file=sys.stderr)
    failed = failed_checks(result)
    print(
        f"проверок {len(result.checks)}, не прошли: {', '.join(failed) or 'нет'}", file=sys.stderr
    )
    return 1 if failed else 0


async def cmd_smoke(args: argparse.Namespace) -> int:
    """Один реальный короткий вызов: проверка контейнера воркера на сервере."""
    settings = get_settings()
    client = _client_for_probe(settings, "off")
    target = _require_configured(client, args.role)
    req = Request(
        role=args.role,
        messages=[
            Message("system", BRIEF_SYSTEM),
            # Метка обходит кэш провайдера по тексту: smoke проверяет инференс, а не кэш.
            Message("user", f"{BRIEF_USER}\n\n[smoke {uuid.uuid4().hex[:10]}]"),
        ],
        response_format="json_object",
        reasoning="off",
        max_output_tokens=300,
        temperature=0.0,
        stage="smoke",
        deadline=Deadline.after(args.deadline_s),
        prompt=("probe", PROBE_VERSION),
    )
    started = time.monotonic()
    try:
        resp = await client.complete(req, parse=ProbeBrief)
    except LlmError as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "provider": target.provider_name,
                    "host": redact_url(target.provider.base_url()),
                    "model": target.model,
                    "error": e.code,
                    "message": str(e)[:300],
                    "limiter": getattr(client.limiter, "name", "?"),
                },
                ensure_ascii=False,
            )
        )
        await client.aclose()
        return 1
    await client.aclose()
    print(
        json.dumps(
            {
                "ok": True,
                "provider": target.provider_name,
                "host": redact_url(target.provider.base_url()),
                "model": resp.model,
                "latency_ms": resp.latency_ms,
                "wall_ms": int((time.monotonic() - started) * 1000),
                "attempts": resp.attempts,
                "quota_wait_ms": resp.quota_wait_ms,
                "usage": _usage_dict(resp),
                "parsed": resp.parsed.model_dump() if isinstance(resp.parsed, BaseModel) else None,
                "limiter": getattr(client.limiter, "name", "?"),
            },
            ensure_ascii=False,
        )
    )
    return 0


async def cmd_list_models(args: argparse.Namespace) -> int:
    settings = get_settings()
    client = _client_for_probe(settings, "off")
    target = _require_configured(client, args.role)
    transport = client.transport_for(target)
    assert isinstance(transport, OpenAITransport)
    try:
        ids = await transport.list_models()
    finally:
        await client.aclose()
    for model_id in ids:
        marker = (
            "  ← настроена" if model_id in {r.model for r in client.models.roles.values()} else ""
        )
        print(f"{model_id}{marker}")
    return 0


# ----- лимитер из нескольких процессов -----


async def _limiter_worker(args: argparse.Namespace) -> int:
    """Дочерний процесс: берёт аренды в общем лимитере и пишет интервалы в список Valkey."""
    import redis.asyncio as aioredis

    quota = Quota(args.concurrency, args.rpm, args.tpm)
    limiter = ValkeyLimiter(
        args.valkey_url, quota, lease_ttl_s=30, max_wait_s=120, prefix=args.prefix
    )
    log = aioredis.Redis.from_url(args.valkey_url)
    key = f"{args.prefix}:check"
    try:
        for _ in range(args.requests):
            lease = await limiter.acquire(key, 100, deadline=Deadline.after(120))
            start = time.time()
            await asyncio.sleep(args.hold_ms / 1000)
            end = time.time()
            await limiter.release(lease, actual_tokens=80)
            await log.rpush(
                f"{args.prefix}:intervals",
                json.dumps(
                    {"pid": os.getpid(), "start": start, "end": end, "wait_ms": lease.wait_ms}
                ),
            )
    finally:
        await limiter.aclose()
        await log.aclose()  # type: ignore[attr-defined]
    return 0


def cmd_limiter(args: argparse.Namespace) -> int:
    """Родитель: запускает N процессов, затем проверяет пересечения интервалов и счёт в минуту."""
    import redis

    prefix = f"pd:llmcheck:{uuid.uuid4().hex[:8]}"
    cmd = [
        sys.executable,
        "-m",
        "presentation_designer.llm.probe",
        "limiter-worker",
        "--valkey-url",
        args.valkey_url,
        "--prefix",
        prefix,
        "--requests",
        str(args.requests),
        "--concurrency",
        str(args.concurrency),
        "--rpm",
        str(args.rpm),
        "--tpm",
        str(args.tpm),
        "--hold-ms",
        str(args.hold_ms),
    ]
    started = time.time()
    procs = [subprocess.Popen(cmd) for _ in range(args.processes)]
    codes = [p.wait(timeout=300) for p in procs]
    wall_s = time.time() - started
    r = redis.Redis.from_url(args.valkey_url)
    raw = r.lrange(f"{prefix}:intervals", 0, -1)
    intervals = [json.loads(x) for x in raw]
    for k in r.scan_iter(f"{prefix}:*"):
        r.delete(k)
    events = sorted(
        [(i["start"], 1) for i in intervals] + [(i["end"], -1) for i in intervals],
        key=lambda e: (e[0], e[1]),
    )
    active = peak = 0
    for _, delta in events:
        active += delta
        peak = max(peak, active)
    starts = sorted(i["start"] for i in intervals)
    max_per_minute = 0
    for idx, s in enumerate(starts):
        j = idx
        while j < len(starts) and starts[j] < s + 60:
            j += 1
        max_per_minute = max(max_per_minute, j - idx)
    expected = args.processes * args.requests
    ok = (
        all(c == 0 for c in codes)
        and len(intervals) == expected
        and peak <= args.concurrency
        and max_per_minute <= args.rpm
    )
    print(
        json.dumps(
            {
                "ok": ok,
                "processes": args.processes,
                "requests_total": expected,
                "recorded": len(intervals),
                "peak_concurrency": peak,
                "limit_concurrency": args.concurrency,
                "max_requests_per_minute": max_per_minute,
                "limit_rpm": args.rpm,
                "wall_s": round(wall_s, 2),
                "max_wait_ms": max((i["wait_ms"] for i in intervals), default=0),
                "exit_codes": codes,
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="presentation_designer.llm.probe", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    probe = sub.add_parser("probe", help="полный зонд с отчётом")
    probe.add_argument("--report", default=None, help="куда записать Markdown-отчёт")
    probe.add_argument("--role", default="llm")
    probe.add_argument("--vlm-role", default="vlm")
    probe.add_argument(
        "--concurrency", default="1,2,4", help="уровни одновременности через запятую"
    )
    probe.add_argument("--deadline-s", type=float, default=180.0)
    probe.add_argument("--skip-images", action="store_true")
    probe.add_argument("--documented-concurrency", type=int, default=None)
    probe.add_argument("--documented-rpm", type=int, default=None)
    probe.add_argument("--documented-tpm", type=int, default=None)

    smoke = sub.add_parser("smoke", help="один реальный вызов")
    smoke.add_argument("--role", default="llm")
    smoke.add_argument("--deadline-s", type=float, default=120.0)

    models = sub.add_parser("list-models", help="каталог моделей провайдера")
    models.add_argument("--role", default="llm")

    for name in ("limiter", "limiter-worker"):
        lim = sub.add_parser(name, help="проверка лимитера из нескольких процессов (Valkey)")
        lim.add_argument(
            "--valkey-url", default=os.environ.get("PD_VALKEY_URL", "redis://localhost:6379/0")
        )
        lim.add_argument("--processes", type=int, default=4)
        lim.add_argument("--requests", type=int, default=6, help="запросов на процесс")
        lim.add_argument("--concurrency", type=int, default=2)
        lim.add_argument("--rpm", type=int, default=600)
        lim.add_argument("--tpm", type=int, default=1_000_000)
        lim.add_argument("--hold-ms", type=int, default=300, help="сколько держать аренду")
        lim.add_argument("--prefix", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "probe"):
        if args.command is None:
            args = parser.parse_args(["probe", *(argv or [])])
        return asyncio.run(cmd_probe(args))
    if args.command == "smoke":
        return asyncio.run(cmd_smoke(args))
    if args.command == "list-models":
        return asyncio.run(cmd_list_models(args))
    if args.command == "limiter":
        return cmd_limiter(args)
    if args.command == "limiter-worker":
        return asyncio.run(_limiter_worker(args))
    parser.print_help()
    return 2


__all__ = [
    "Check",
    "Probe",
    "ProbeBrief",
    "ProbePlan",
    "ProbeResult",
    "main",
    "render_report",
    "render_slide_image",
    "yaml_snippet",
]

if __name__ == "__main__":
    sys.exit(main())
