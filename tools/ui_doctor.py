"""Проверка интерфейса вживую: тот ли слайд показан, те ли на нём рамки находок.

Отвечает на вопрос «почему рамки стоят не на том слайде» не рассуждением, а замером: водит
настоящий браузер по ленте миниатюр и сверяет нарисованное с `audit.json` того же задания.
Второй сценарий — перестановка слайдов в черновике редактора: место в ленте и номер слайда в
колоде расходятся, и раньше рамки, счётчик и адрес правки начинали жить каждый своей жизнью.

Playwright тут не используется: на машине разработки может не быть Node, а Chrome есть почти
везде. Поэтому браузер запускается напрямую и управляется по CDP через websocket из
стандартной библиотеки.

Запуск (стек должен быть поднят):
    uv run python tools/ui_doctor.py prj_ab12…
    uv run python tools/ui_doctor.py prj_ab12… --base-url http://localhost:8080 --slides 8

Код возврата 1, если хоть одна сверка не сошлась.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from typing import Any

JsonDict = dict[str, Any]

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def find_chrome() -> str | None:
    env = os.environ.get("PD_CHROME")
    if env and pathlib.Path(env).exists():
        return env
    return next((p for p in CHROME_CANDIDATES if pathlib.Path(p).exists()), None)


class Browser:
    """Chrome в фоновом режиме и минимальный клиент CDP поверх websocket."""

    def __init__(self, width: int = 1600, height: int = 1000, port: int = 9333) -> None:
        chrome = find_chrome()
        if not chrome:
            raise RuntimeError("Chrome не найден: укажите путь в переменной PD_CHROME")
        self.port = port
        self.profile = f"/tmp/pd-ui-doctor-{os.getpid()}"
        self.proc = subprocess.Popen(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={self.profile}",
                f"--window-size={width},{height}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1).read()
                break
            except OSError:
                time.sleep(0.3)
        else:
            raise RuntimeError("Chrome не поднялся")
        targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list").read())
        page = next(t for t in targets if t["type"] == "page")
        self._connect(str(page["webSocketDebuggerUrl"]))
        self.call("Page.enable")
        self.call("Runtime.enable")

    def _connect(self, url: str) -> None:
        _, rest = url.split("://", 1)
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)))
        self.sock.settimeout(60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        self.rest = buf.split(b"\r\n\r\n", 1)[1]
        self.next_id = 0

    def _read(self, n: int) -> bytes:
        while len(self.rest) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("соединение с браузером закрыто")
            self.rest += chunk
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def call(self, method: str, params: JsonDict | None = None) -> JsonDict:
        self.next_id += 1
        data = json.dumps({"id": self.next_id, "method": method, "params": params or {}}).encode()
        mask = os.urandom(4)
        n = len(data)
        head = b"\x81"
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
        while True:
            _, b1 = self._read(2)
            size = b1 & 0x7F
            if size == 126:
                size = struct.unpack(">H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._read(8))[0]
            message = json.loads(self._read(size).decode())
            if message.get("id") == self.next_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return dict(message.get("result") or {})

    def open(self, url: str, wait_s: float = 8.0) -> None:
        self.call("Page.navigate", {"url": url})
        time.sleep(wait_s)

    def js(self, expression: str) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        return (result.get("result") or {}).get("value")

    def close(self) -> None:
        self.proc.terminate()


def issues_by_slide(job: str, variant: str, revision: int | None = None) -> dict[int, int]:
    """Рамки последней ревизии: интерфейс показывает её же, а не первую сборку."""
    base = ROOT / "artifacts" / "jobs" / job / variant
    if revision is None:
        revisions = sorted(int(p.name[1:]) for p in base.glob("r*") if p.name[1:].isdigit())
        revision = revisions[-1] if revisions else 1
    path = base / f"r{revision}" / "audit.json"
    if not path.is_file():
        return {}
    out: dict[int, int] = {}
    for issue in json.loads(path.read_text(encoding="utf-8")).get("issues") or []:
        index = issue.get("slide_index")
        if index is not None and issue.get("bbox"):
            out[int(index)] = out.get(int(index), 0) + 1
    return out


def probe_filmstrip(b: Browser, expected: dict[int, int], limit: int) -> list[str]:
    """Каждая миниатюра: тот ли слайд открылся и столько ли на нём рамок, сколько в отчёте."""
    problems: list[str] = []
    total = int(b.js("document.querySelectorAll('[data-testid=thumb-strip] button').length") or 0)
    print(f"миниатюр в ленте: {total}")
    for position in range(min(total, limit)):
        b.js(f"document.querySelectorAll('[data-testid=thumb-strip] button')[{position}].click()")
        time.sleep(1.2)
        counter = b.js("document.querySelector('[data-testid=slide-counter]')?.textContent") or ""
        boxes = int(b.js("document.querySelectorAll('[data-testid^=issue-box-]').length") or 0)
        active = int(
            b.js(
                "[...document.querySelectorAll('[data-testid=thumb-strip] button')]"
                ".findIndex(x => x.dataset.active === 'true')"
            )
            or 0
        )
        want = expected.get(position, 0)
        ok = boxes == want and f"Слайд {position + 1} " in counter and active == position
        print(
            f"  место {position + 1:>2}: «{counter}», активна {active + 1}, "
            f"рамок {boxes} против {want} {'✓' if ok else '✗'}"
        )
        if not ok:
            problems.append(
                f"место {position + 1}: счётчик «{counter}», активна {active + 1}, "
                f"рамок {boxes} против {want}"
            )
    return problems


def probe_reorder(b: Browser, expected: dict[int, int], slide: int = 5) -> list[str]:
    """Слайд переставлен в черновике: рамки, выбор и адрес правки должны остаться при нём."""
    problems: list[str] = []
    b.js(f"document.querySelectorAll('[data-testid=thumb-strip] button')[{slide}].click()")
    time.sleep(1.2)
    want = expected.get(slide, 0)
    move = """(() => {
      const b = document.querySelectorAll('[data-testid=thumb-strip] button')[%d];
      if (!b) return false;
      b.focus();
      b.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowUp', altKey: true, bubbles: true}));
      return true;
    })()"""
    if not b.js(move % slide):
        print("  перестановка недоступна: редактор не готов")
        return problems
    time.sleep(1.0)
    b.js(move % (slide - 1))
    time.sleep(1.5)
    active = int(
        b.js(
            "[...document.querySelectorAll('[data-testid=thumb-strip] button')]"
            ".findIndex(x => x.dataset.active === 'true')"
        )
        or 0
    )
    boxes = int(b.js("document.querySelectorAll('[data-testid^=issue-box-]').length") or 0)
    chip = b.js("document.querySelector('[data-testid=slide-target]')?.textContent") or ""
    print(
        f"  слайд {slide + 1} поднят на два места: активна {active + 1}, "
        f"рамок {boxes} против {want}, чип «{chip.split('правка')[0].strip()}»"
    )
    if boxes != want:
        problems.append(f"после перестановки рамок {boxes}, а у слайда {slide + 1} их {want}")
    if active != slide - 2:
        problems.append(f"после перестановки выбрана миниатюра {active + 1}, ожидали {slide - 1}")
    if chip and f"Слайд {slide + 1}" not in chip:
        problems.append(f"адрес правки указывает не на тот слайд: «{chip}»")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Проверка соответствия слайда и рамок в интерфейсе"
    )
    parser.add_argument("project", help="идентификатор проекта (prj_…)")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--slides", type=int, default=12, help="сколько миниатюр обойти")
    parser.add_argument("--skip-reorder", action="store_true")
    args = parser.parse_args(argv)

    project = json.loads(
        urllib.request.urlopen(f"{args.base_url}/api/projects/{args.project}").read()
    )
    job_id = project.get("job_id")
    if not job_id:
        print("у проекта нет задания: показывать нечего")
        return 2
    result = json.loads(urllib.request.urlopen(f"{args.base_url}/api/generations/{job_id}").read())
    variants = [v["variant_id"] for v in result["variants"] if v.get("slide_count")]
    if not variants:
        print("ни один вариант не собран")
        return 2
    variant = variants[0]
    expected = issues_by_slide(job_id, variant)
    print(f"проект {args.project} | задание {job_id} | вариант {variant}")
    print(f"находок с рамками в отчёте: {sum(expected.values())}")

    browser = Browser()
    problems: list[str] = []
    try:
        browser.open(f"{args.base_url}/project?id={args.project}")
        problems += probe_filmstrip(browser, expected, args.slides)
        if not args.skip_reorder:
            print("перестановка слайда в черновике:")
            problems += probe_reorder(browser, expected)
    finally:
        browser.close()

    print("\n=== итог ===")
    if problems:
        print(f"расхождений: {len(problems)}")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("интерфейс показывает те слайды и те рамки, что записаны в отчёте")
    return 0


if __name__ == "__main__":
    sys.exit(main())
