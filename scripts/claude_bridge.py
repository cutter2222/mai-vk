"""Мост для быстрых локальных прогонов: OpenAI-совместимый /v1/chat/completions поверх Claude Code.

Зачем: qwen на OpenRouter отвечает медленно, а при доработке вёрстки нужно много прогонов.
Мост принимает запросы стенда как обычный провайдер и отвечает слабой моделью Claude (Haiku)
через CLI `claude -p` с подпиской разработчика. Проверка на qwen — отдельным прогоном.

Запуск на хосте (не в Docker: CLI и вход в аккаунт — на машине разработчика):
    uv run python scripts/claude_bridge.py --port 8765
В .env (стенд ходит на хост через host.docker.internal):
    PD_CLAUDE_BRIDGE_URL=http://host.docker.internal:8765/v1
    PD_CLAUDE_BRIDGE_KEY=local
    PD_MODELS__ROLES__LLM__PROVIDER=claude-bridge
    PD_MODELS__ROLES__LLM__MODEL=claude-haiku-4-5
    (и то же для VLM)

Что делает с запросом: системные сообщения → --system-prompt; остальные сообщения — один
ход пользователя (текст и картинки); response_format json_schema → --json-schema (ответ
инструментом StructuredOutput); json_object — просьба ответить одним объектом и снятие
ограды ```json. Рассуждение выключено (MAX_THINKING_TOKENS=0), инструменты не даются.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MODELS = {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus"}
LOCK = threading.Lock()
STATS = {"calls": 0, "errors": 0, "seconds": 0.0}


def find_cli() -> str:
    """CLI из PATH или из расширения VS Code (свежая версия)."""
    env = os.environ.get("CLAUDE_CLI")
    if env:
        return env
    for name in ("claude",):
        for d in os.environ.get("PATH", "").split(os.pathsep):
            p = Path(d) / name
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
    found = sorted(
        glob.glob(
            str(
                Path.home()
                / ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude"
            )
        )
    )
    if not found:
        sys.exit("не найден CLI claude: задайте CLAUDE_CLI")
    return found[-1]


DEFAULT_MODEL = "haiku"


def model_alias(name: str) -> str:
    """Модель Claude по имени из запроса; чужое имя (qwen…) — модель по умолчанию
    (`--model`): так записи для тестов можно делать более сильной моделью, не меняя ключей."""
    low = (name or "").lower()
    for key, alias in MODELS.items():
        if key in low:
            return alias
    return DEFAULT_MODEL


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content or [] if p.get("type") == "text")


def _image_block(part: dict[str, Any]) -> dict[str, Any] | None:
    url = (part.get("image_url") or {}).get("url", "")
    m = re.match(r"data:(image/[\w.+-]+);base64,(.*)", url, re.S)
    if not m:
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": m[1], "data": m[2]}}


def to_cli_input(body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """(системный промпт, блоки единственного хода пользователя)."""
    system: list[str] = []
    blocks: list[dict[str, Any]] = []
    for msg in body.get("messages") or []:
        role, content = msg.get("role"), msg.get("content")
        if role in ("system", "developer"):
            system.append(_text_of(content))
            continue
        prefix = "Твой предыдущий ответ:\n" if role == "assistant" else ""
        if isinstance(content, str):
            blocks.append({"type": "text", "text": prefix + content})
            continue
        for part in content or []:
            if part.get("type") == "text":
                blocks.append({"type": "text", "text": prefix + part.get("text", "")})
            elif part.get("type") == "image_url":
                img = _image_block(part)
                if img:
                    blocks.append(img)
    fmt = body.get("response_format") or {}
    if fmt.get("type") == "json_object":
        blocks.append({"type": "text", "text": "Ответь одним JSON-объектом без пояснений."})
    if not blocks:
        blocks.append({"type": "text", "text": "."})
    return "\n\n".join(s for s in system if s) or "Отвечай точно по просьбе.", blocks


def _strip_fence(text: str) -> str:
    m = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", text, re.S)
    return m[1] if m else text


def call_claude(cli: str, body: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    system, blocks = to_cli_input(body)
    model = model_alias(str(body.get("model") or ""))
    cmd = [
        cli, "-p", "--model", model,
        "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
        "--tools", "", "--system-prompt", system,
        "--no-session-persistence", "--setting-sources", "",
    ]  # fmt: skip
    fmt = body.get("response_format") or {}
    schema = (
        (fmt.get("json_schema") or {}).get("schema") if fmt.get("type") == "json_schema" else None
    )
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
    line = json.dumps(
        {"type": "user", "message": {"role": "user", "content": blocks}}, ensure_ascii=False
    )
    env = {**os.environ, "MAX_THINKING_TOKENS": "0"}
    proc = subprocess.run(
        cmd, input=line + "\n", capture_output=True, text=True, timeout=timeout_s, env=env,
        cwd=os.environ.get("TMPDIR", "/tmp"),
    )  # fmt: skip
    structured: Any = None
    text = ""
    result: dict[str, Any] = {}
    for raw in proc.stdout.splitlines():
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for part in ev["message"].get("content") or []:
                if part.get("type") == "tool_use" and part.get("name") == "StructuredOutput":
                    structured = part.get("input")
                elif part.get("type") == "text":
                    text += part.get("text", "")
        elif ev.get("type") == "result":
            result = ev
    if structured is None and result.get("structured_output") is not None:
        structured = result["structured_output"]
    if result.get("is_error") or (structured is None and not text and not result.get("result")):
        detail = (result.get("result") or proc.stderr or proc.stdout)[-400:]
        raise RuntimeError(f"claude: {detail}")
    content = (
        json.dumps(structured, ensure_ascii=False)
        if structured is not None
        else _strip_fence(text or str(result.get("result") or ""))
    )
    usage = result.get("usage") or {}
    # CLI делит вход на три части: свежий, прочитанный из кэша и записанный в кэш; первый
    # запрос сессии почти весь «записывается в кэш», без него prompt_tokens ≈ 2.
    prompt = sum(
        int(usage.get(k) or 0)
        for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    )
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": f"claude-{model}",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": prompt + int(usage.get("output_tokens") or 0),
        },
    }


class Handler(BaseHTTPRequestHandler):
    cli = ""
    token = ""
    timeout_s = 180.0

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        return not self.token or self.headers.get("authorization") == f"Bearer {self.token}"

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/models"):
            ids = ["claude-haiku-4-5", "claude-sonnet", "claude-opus"]
            self._send(200, {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]})
        else:
            self._send(200, {"ok": True, **STATS})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(401, {"error": {"message": "неверный ключ моста"}})
            return
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": "только /v1/chat/completions"}})
            return
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        started = time.monotonic()
        try:
            payload = call_claude(self.cli, body, self.timeout_s)
        except subprocess.TimeoutExpired:
            self._track(started, error=True)
            self._send(504, {"error": {"message": "claude не ответил вовремя"}})
            return
        except Exception as e:
            self._track(started, error=True)
            self._send(502, {"error": {"message": str(e)[:400]}})
            return
        self._track(started)
        self._send(200, payload)

    def _track(self, started: float, *, error: bool = False) -> None:
        spent = time.monotonic() - started
        with LOCK:
            STATS["calls"] += 1
            STATS["errors"] += int(error)
            STATS["seconds"] += spent
        mark = "ошибка" if error else "ок"
        sys.stderr.write(f"{time.strftime('%H:%M:%S')} {mark} {spent:.1f} с\n")

    def log_message(self, format: str, *args: Any) -> None:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", default=os.environ.get("PD_CLAUDE_BRIDGE_KEY", "local"))
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument(
        "--model",
        choices=sorted(MODELS),
        default=DEFAULT_MODEL,
        help="модель для запросов с чужим именем модели (по умолчанию haiku)",
    )
    args = ap.parse_args()
    globals()["DEFAULT_MODEL"] = args.model
    Handler.cli = find_cli()
    Handler.token = args.token
    Handler.timeout_s = args.timeout
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    sys.stderr.write(f"мост Claude на http://{args.host}:{args.port}/v1 ({Handler.cli})\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
