"""Девять презентаций одним прогоном: три шаблона × три варианта на одном содержании.

Требование ТЗ к сдаче отбора — показать три варианта вёрстки на трёх разных шаблонах при одном
и том же контенте, и уложиться в пять минут на колоду. Скрипт делает это через тот же API, что
и интерфейс: загружает шаблоны, импортирует содержание один раз, запускает генерацию на каждом
шаблоне и ждёт. Ничего не мокает: замер — это время от запроса до терминального состояния
задания, а не сумма внутренних этапов.

Итог: `runs/matrix/<метка>/report.json` с таймингами, находками аудита и именами артефактов,
рядом — `summary.md` таблицей и сами файлы колод, если позвать с `--download`.

    uv run python scripts/run_matrix.py --api http://localhost:8080
    uv run python scripts/run_matrix.py --templates "data/organizers/*.pptx" --download
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import mimetypes
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
JsonDict = dict[str, Any]
DEFAULT_TEMPLATES = [
    "data/organizers/VK Tech шаблон.pptx",
    "data/organizers/VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
    "data/organizers/Шаблон презентации VK Education.pptx",
]
CONTENT_DIR = ROOT / "examples" / "content"
VARIANTS = ["compact", "balanced", "detailed"]
# Рамка ТЗ: пять минут на колоду.
FRAME_S = 300


def _request(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
    timeout: int = 120,
) -> JsonDict:
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return json.loads(body.decode()) if body else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode()[:400]
        raise SystemExit(f"{method or 'GET'} {url}: HTTP {error.code} {detail}") from error


def get(api: str, path: str) -> JsonDict:
    return _request(f"{api}{path}")


def post_json(api: str, path: str, payload: JsonDict) -> JsonDict:
    return _request(
        f"{api}{path}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )


def post_files(
    api: str, path: str, files: list[tuple[str, pathlib.Path]], fields: dict[str, str]
) -> JsonDict:
    """multipart без зависимостей: тот же запрос, что шлёт браузер."""
    boundary = f"----matrix{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        ).encode()
    for field, file_path in files:
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
            f'filename="{file_path.name}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode()
        body += file_path.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return _request(
        f"{api}{path}",
        data=bytes(body),
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
        timeout=600,
    )


def wait(
    api: str, path: str, done: str, *, label: str, limit_s: int = 900
) -> tuple[JsonDict, float]:
    """Ждёт терминального состояния, возвращает ответ и время ожидания в секундах."""
    started = time.time()
    last = ""
    while time.time() - started < limit_s:
        data = get(api, path)
        status = str(data.get("status") or "")
        stage = str(data.get("stage") or "")
        line = f"{status}/{stage}" if stage else status
        if line != last:
            print(f"    {label}: {line} ({time.time() - started:.0f} c)", flush=True)
            last = line
        if status in {"succeeded", "needs_review", "failed", "canceled"} or status == done:
            return data, time.time() - started
        time.sleep(3)
    raise SystemExit(f"{label}: не дождались за {limit_s} c")


def import_content(api: str) -> tuple[str, float]:
    files = [
        ("files", p)
        for p in sorted(CONTENT_DIR.iterdir())
        if p.is_file() and p.name != "brief.json"
    ]
    brief = (CONTENT_DIR / "brief.json").read_text()
    print(f"  содержание: {len(files)} файлов", flush=True)
    created = post_files(api, "/api/content", files, {"brief": brief})
    package_id = str(created["package_id"])
    data, seconds = wait(api, f"/api/content/{package_id}", "succeeded", label="импорт")
    if data.get("status") != "succeeded":
        raise SystemExit(f"импорт не удался: {json.dumps(data.get('error'), ensure_ascii=False)}")
    package = data.get("package") or {}
    print(
        f"  импорт за {seconds:.0f} c: блоков {len(package.get('blocks') or [])}, "
        f"фактов {len(package.get('facts') or [])}",
        flush=True,
    )
    return package_id, seconds


def analyze_template(api: str, path: pathlib.Path) -> tuple[str, float, JsonDict]:
    print(f"  шаблон {path.name} ({path.stat().st_size / 1024 / 1024:.0f} МБ)", flush=True)
    created = post_files(api, "/api/templates", [("file", path)], {})
    template_id = str(created["template_id"])
    data, seconds = wait(api, f"/api/templates/{template_id}", "succeeded", label="разбор")
    if data.get("status") != "succeeded":
        raise SystemExit(f"разбор не удался: {json.dumps(data.get('error'), ensure_ascii=False)}")
    profile = data.get("profile") or {}
    stats = profile.get("stats") or {}
    print(
        f"  разбор за {seconds:.0f} c{' (из кэша)' if created.get('cached') else ''}: "
        f"композиций {len(profile.get('patterns') or [])}, слайдов {stats.get('slides')}",
        flush=True,
    )
    return template_id, seconds, profile


def generate(
    api: str, template_id: str, package_id: str, *, contextual: bool
) -> tuple[JsonDict, float]:
    payload = {
        "schema_version": "1.2",
        "template_id": template_id,
        "package_id": package_id,
        "idempotency_key": f"matrix-{uuid.uuid4().hex[:12]}",
        "settings": {
            "slide_count": {"min": 10, "max": 15},
            "language": "ru",
            "variants": VARIANTS,
            "generate_images": False,
            "run_contextual_audit": contextual,
            "force_regenerate": True,
        },
    }
    created = post_json(api, "/api/generations", payload)
    job_id = str(created["job_id"])
    label = f"сборка {job_id[:12]}"
    data, seconds = wait(api, f"/api/generations/{job_id}", "succeeded", label=label)
    return data, seconds


def summarize(result: JsonDict, seconds: float) -> JsonDict:
    variants = []
    for variant in result.get("variants") or []:
        audit = variant.get("audit") or {}
        variants.append(
            {
                "variant_id": variant.get("variant_id"),
                "status": variant.get("status"),
                "slides": variant.get("slide_count"),
                "issues": audit.get("issues_total"),
                "blocking": audit.get("blocking"),
                "coverage_complete": audit.get("coverage_complete"),
                "pptx": (variant.get("artifacts") or {}).get("pptx"),
                "pdf": (variant.get("artifacts") or {}).get("pdf"),
            }
        )
    totals = (result.get("metrics") or {}).get("totals") or {}
    return {
        "job_id": result.get("job_id"),
        "status": result.get("status"),
        "seconds": round(seconds, 1),
        "within_frame": seconds <= FRAME_S,
        "duration_ms": totals.get("duration_ms"),
        "llm_calls": totals.get("llm_calls"),
        "execution_mode": (result.get("execution_mode") or {}).get("mode"),
        "variants": variants,
        "warnings": [w.get("code") for w in result.get("warnings") or []],
    }


def download(api: str, job_id: str, name: str, out: pathlib.Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    url = f"{api}/api/generations/{job_id}/artifacts/{urllib.parse.quote(name)}"
    with urllib.request.urlopen(url, timeout=600) as response:
        out.write_bytes(response.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8080")
    parser.add_argument(
        "--templates", nargs="*", default=None, help="пути или маски файлов шаблонов"
    )
    parser.add_argument(
        "--contextual", action="store_true", help="включить контекстный аудит моделью"
    )
    parser.add_argument("--download", action="store_true", help="скачать колоды рядом с отчётом")
    parser.add_argument("--out", default=None, help="каталог отчёта")
    args = parser.parse_args()

    patterns = args.templates or DEFAULT_TEMPLATES
    templates: list[pathlib.Path] = []
    for pattern in patterns:
        absolute = pattern if pathlib.Path(pattern).is_absolute() else str(ROOT / pattern)
        matches = sorted(glob.glob(absolute))
        templates += [pathlib.Path(m) for m in matches]
    if not templates:
        raise SystemExit(f"шаблоны не найдены: {patterns}")

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = pathlib.Path(args.out) if args.out else ROOT / "runs" / "matrix" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    health = get(args.api, "/api/health")
    mode = (health.get("execution_mode") or {}).get("mode")
    print(f"стенд: {health.get('status')}, режим {mode}", flush=True)

    package_id, import_s = import_content(args.api)

    report = {
        "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "api": args.api,
        "frame_s": FRAME_S,
        "content": {"package_id": package_id, "seconds": round(import_s, 1)},
        "decks": [],
    }
    for path in templates:
        print(f"\n=== {path.name}", flush=True)
        template_id, analyze_s, profile = analyze_template(args.api, path)
        result, seconds = generate(args.api, template_id, package_id, contextual=args.contextual)
        deck = {
            "template": path.name,
            "template_id": template_id,
            "analyze_seconds": round(analyze_s, 1),
            "patterns": len(profile.get("patterns") or []),
            **summarize(result, seconds),
        }
        report["decks"].append(deck)
        print(
            f"  итог: {deck['status']} за {deck['seconds']} c "
            f"({'в рамке' if deck['within_frame'] else 'ВЫШЛИ ЗА 5 МИНУТ'}), "
            + ", ".join(
                f"{v['variant_id']}: {v['slides']} слайдов, находок {v['issues']}"
                for v in deck["variants"]
            ),
            flush=True,
        )
        if args.download:
            for variant in deck["variants"]:
                for kind in ("pptx", "pdf"):
                    name = variant.get(kind)
                    if name:
                        target = out_dir / path.stem / f"{variant['variant_id']}.{kind}"
                        download(args.api, deck["job_id"], name, target)

    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (out_dir / "summary.md").write_text(_summary_md(report))
    print(f"\nотчёт: {out_dir / 'report.json'}\nсводка: {out_dir / 'summary.md'}", flush=True)
    worst = max((d["seconds"] for d in report["decks"]), default=0)
    print(f"худшая колода: {worst:.0f} c при рамке {FRAME_S} c", flush=True)
    return 0 if all(d["within_frame"] and d["status"] != "failed" for d in report["decks"]) else 1


def _summary_md(report: JsonDict) -> str:
    lines = [
        "# Девять презентаций: три шаблона × три варианта",
        "",
        f"Прогон {report['created_at']}, рамка ТЗ — {report['frame_s']} c на колоду.",
        f"Импорт содержания один раз: {report['content']['seconds']} c.",
        "",
        "| шаблон | композиций | разбор, c | сборка, c | в рамке | вариант | слайдов "
        "| находок | покрытие |",
        "| --- | ---: | ---: | ---: | :--: | --- | ---: | ---: | :--: |",
    ]
    for deck in report["decks"]:
        for i, variant in enumerate(deck["variants"]):
            head = (
                f"| {deck['template']} | {deck['patterns']} | {deck['analyze_seconds']} | "
                f"{deck['seconds']} | {'да' if deck['within_frame'] else 'нет'} "
                if i == 0
                else "| | | | | "
            )
            lines.append(
                head
                + f"| {variant['variant_id']} | {variant['slides']} | {variant['issues']} | "
                + ("полное" if variant["coverage_complete"] else "неполное")
                + " |"
            )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
