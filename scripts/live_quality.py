"""Живая проверка качества генерации: четыре сценария «материал + шаблон» через API.

Каждый сценарий — новый проект, импорт материалов, генерация трёх вариантов и контактные
листы всех слайдов из PDF (смотреть глазами: аудит не видит пустых по смыслу слайдов,
мусорных подписей и повторов). В `runs/quality/<метка>/` — листы, PNG слайдов, итог
`summary.json` и сводка находок аудита по видам.

Сценарии покрывают четыре шаблона организаторов и три вида входа:

* ``ocean`` — VK Education, markdown с числами, этапами и бюджетом, точно 8 слайдов;
* ``notify`` — VK Tech, пример ``examples/content`` (docx, xlsx, md, png);
* ``topic`` — VK WorkSpace, одна тема без материалов (концепция);
* ``lct`` — ЛЦТ, markdown с таблицей по районам и экономикой.

    uv run python scripts/live_quality.py all --label after-fix
    uv run python scripts/live_quality.py lct --label lct-check --variants balanced

Нужен запущенный стек (``make up``) с настроенной моделью и загруженными шаблонами
организаторов (``make organizer-data`` и загрузка через интерфейс или API).
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import httpx
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parents[1]
QUALITY = ROOT / "examples" / "quality"
CONTENT = ROOT / "examples" / "content"
TEMPLATE_NAMES = {
    "edu": "Шаблон презентации VK Education.pptx",
    "workspace": "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
    "tech": "VK Tech шаблон.pptx",
    "lct": "ЛЦТ2026 Шаблон презентации.pptx",
}
SCENARIOS: dict[str, dict[str, Any]] = {
    "ocean": {
        "template": "edu",
        "files": [QUALITY / "ocean-expedition.md"],
        "brief": {
            "purpose": "project",
            "title": "Бездна: проект глубоководной экспедиции",
            "audience": "Научный совет: океанологи, инженеры и специалисты по охране природы",
            "goal": "Согласовать пилотную экспедицию и бюджет на основе вымышленного учебного "
            "сценария",
            "language": "ru",
            "tone": "Научно-популярный, ясный, без рекламных обещаний",
            "must_include": [
                "Явная маркировка: вымышленный учебный проект, все показатели плановые",
                "Все числовые параметры, состав команды, этапы и бюджет из материала",
                "Различия ROV, AUV и CTD; условия безопасности и получения разрешений",
                "Плановые критерии успеха, условие публикации и решение научного совета",
            ],
            "avoid": ["Не добавлять неподтверждённые факты и источники"],
        },
        "slides": {"exact": 8},
    },
    "notify": {
        "template": "tech",
        "files": [
            CONTENT / "overview.docx",
            CONTENT / "metrics.xlsx",
            CONTENT / "notes.md",
            CONTENT / "openrate_chart.png",
        ],
        "brief": "examples/content/brief.json",
        "slides": {"min": 10, "max": 12},
    },
    "topic": {
        "template": "workspace",
        "files": [],
        "brief": {
            "purpose": "initiative",
            "title": "Как устроена гибридная работа в команде",
            "audience": "Руководители отделов",
            "goal": "Договориться о правилах гибридной работы",
            "language": "ru",
            "tone": "деловой",
            "must_include": [],
            "avoid": [],
        },
        "slides": {"exact": 8},
    },
    "lct": {
        "template": "lct",
        "files": [QUALITY / "smart-stops.md"],
        "brief": {
            "purpose": "project",
            "title": "Умные остановки: пилот в трёх районах",
            "audience": "Жюри хакатона и представители транспортного департамента",
            "goal": "Показать результаты пилота и получить поддержку масштабирования",
            "language": "ru",
            "tone": "уверенный, конкретный",
            "must_include": ["результаты пилота", "экономика", "план масштабирования"],
            "avoid": [],
        },
        "slides": {"min": 9, "max": 11},
    },
}
FINAL = {"succeeded", "failed", "cancelled", "partial", "needs_review"}
# PDFium не потокобезопасен: листы сценариев из параллельных потоков роняли процесс (SIGSEGV).
PDFIUM = threading.Lock()


class Api:
    def __init__(self, base: str) -> None:
        self.client = httpx.Client(base_url=f"{base.rstrip('/')}/api", timeout=180)

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        if response.is_error:
            print(response.text, file=sys.stderr)
        response.raise_for_status()
        return response.json()

    def wait(self, job_id: str, limit: float = 1500) -> dict[str, Any]:
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            job = self.call("GET", f"/jobs/{job_id}")
            if job["status"] in FINAL:
                return dict(job)
            time.sleep(4)
        raise TimeoutError(job_id)

    def template_id(self, name: str) -> str:
        items = self.call("GET", "/templates")
        items = items if isinstance(items, list) else items.get("items", [])
        for item in items:
            if item.get("name") == name and item.get("status") == "succeeded":
                return str(item["template_id"])
        raise SystemExit(f"шаблон «{name}» не загружен или не проанализирован")


def contact_sheet(pdf_path: pathlib.Path, out: pathlib.Path, label: str) -> dict[str, Any]:
    """Контактный лист и PNG слайдов; символы за краем страницы — обрезанный текст."""
    report: dict[str, Any] = {"clipped": []}
    with PDFIUM, pdfium.PdfDocument(pdf_path) as pdf:
        cols, tw, th = 3, 620, 349
        rows = (len(pdf) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * (tw + 10), rows * (th + 26)), "#c8c8c8")
        draw = ImageDraw.Draw(sheet)
        for i, page in enumerate(pdf):
            text = page.get_textpage()
            width, height = page.get_size()
            for c in range(text.count_chars()):
                left, bottom, right, top = text.get_charbox(c)
                if left < -1 or bottom < -1 or right > width + 1 or top > height + 1:
                    report["clipped"].append(i + 1)
                    break
            text.close()
            image = page.render(scale=1.4).to_pil().convert("RGB")
            image.save(out / f"{label}-{i + 1:02}.png")
            image.thumbnail((tw, th))
            x, y = (i % cols) * (tw + 10), (i // cols) * (th + 26) + 22
            sheet.paste(image, (x, y))
            draw.text((x + 4, y - 18), f"{label} / {i + 1}", fill="black")
            page.close()
        sheet.save(out / f"{label}-contact.png")
        report["pages"] = len(pdf)
    return report


def run(api: Api, name: str, out: pathlib.Path, variants: list[str], seed: int) -> dict[str, Any]:
    spec = SCENARIOS[name]
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    brief = spec["brief"]
    if isinstance(brief, str):
        brief = json.loads((ROOT / brief).read_text())
    brief = {k: v for k, v in brief.items() if k != "slide_count"}
    template = api.template_id(TEMPLATE_NAMES[spec["template"]])
    project = api.call("POST", "/projects", json={"title": f"QA {name}", "brief": brief})
    pid = project["project_id"]
    file_ids: list[str] = []
    if spec["files"]:
        files = [("files", (p.name, p.read_bytes())) for p in spec["files"]]
        file_ids = [f["file_id"] for f in api.call("POST", f"/projects/{pid}/files", files=files)]
    package = api.call("POST", "/content", json={"file_ids": file_ids, "brief": brief})
    if api.wait(package["job_id"])["status"] != "succeeded":
        raise SystemExit(f"{name}: импорт не удался")
    request = {
        "schema_version": "1.2",
        "template_id": template,
        "package_id": package["package_id"],
        "settings": {
            "slide_count": spec["slides"],
            "language": "ru",
            "variants": variants,
            "generate_images": False,
            "run_contextual_audit": False,
            "seed": seed,
            "force_regenerate": True,
        },
        "idempotency_key": f"quality-{name}-{pid}",
    }
    job_id = api.call("POST", "/generations", json=request)["job_id"]
    api.call(
        "PATCH",
        f"/projects/{pid}",
        json={
            "template_id": template,
            "package_id": package["package_id"],
            "job_id": job_id,
            "chosen_variant": variants[0],
        },
    )
    return report_job(api, name, out, variants, job_id, pid, started)


def report_job(
    api: Api,
    name: str,
    out: pathlib.Path,
    variants: list[str],
    job_id: str,
    pid: str = "",
    started: float | None = None,
) -> dict[str, Any]:
    """Листы, аудит и сводка по заданию генерации (новому или уже готовому)."""
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic() if started is None else started
    job = api.wait(job_id)
    result = api.call("GET", f"/generations/{job_id}")
    reports = []
    for variant in variants:
        entry: dict[str, Any] = next(
            (v for v in result["variants"] if v["variant_id"] == variant), {}
        )
        report: dict[str, Any] = {"variant": variant, "status": entry.get("status")}
        if entry.get("error"):
            report["error"] = entry["error"]
        prefix = f"{variant}/r{entry.get('revision', 1)}"
        pdf = api.client.get(f"/generations/{job_id}/artifacts/{prefix}/deck.pdf")
        if pdf.is_success:
            (out / f"{variant}.pdf").write_bytes(pdf.content)
            report.update(contact_sheet(out / f"{variant}.pdf", out, variant))
            audit = api.client.get(f"/generations/{job_id}/artifacts/{prefix}/audit.json")
            if audit.is_success:
                issues = audit.json().get("issues") or []
                report["errors"] = dict(
                    collections.Counter(
                        i["check_id"] for i in issues if i["severity"] in ("error", "blocking")
                    )
                )
        reports.append(report)
    summary = {
        "scenario": name,
        "project_url": f"{str(api.client.base_url).removesuffix('/api/')}/project?id={pid}",
        "job_id": job_id,
        "status": job["status"],
        "elapsed_s": round(time.monotonic() - started),
        "generation_s": _span_s(job),
        "variants": reports,
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def _span_s(job: dict[str, Any]) -> int | None:
    """Длительность задания генерации по его отметкам времени."""
    try:
        start = datetime.fromisoformat(str(job["started_at"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(job["finished_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    return round((end - start).total_seconds())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("scenario", choices=[*SCENARIOS, "all"])
    parser.add_argument("--label", default=time.strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--api", default="http://localhost:8080")
    parser.add_argument("--variants", default="compact,balanced,detailed")
    parser.add_argument("--seed", type=int, default=240926)
    parser.add_argument(
        "--job",
        action="append",
        default=[],
        metavar="СЦЕНАРИЙ=JOB_ID",
        help="не генерировать заново, а собрать листы и сводку по готовому заданию",
    )
    args = parser.parse_args()
    api = Api(args.api)
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    base = ROOT / "runs" / "quality" / args.label
    variants = args.variants.split(",")
    jobs = dict(item.split("=", 1) for item in args.job)

    def one(name: str) -> dict[str, Any]:
        if name in jobs:
            return report_job(api, name, base / name, variants, jobs[name])
        return run(api, name, base / name, variants, args.seed)

    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        summaries = list(pool.map(one, names))
    total: collections.Counter[str] = collections.Counter()
    for summary in summaries:
        for report in summary["variants"]:
            total.update(report.get("errors") or {})
            print(
                f"{summary['scenario']:>7} {report['variant']:>9} "
                f"слайдов {report.get('pages', '—')!s:>3} "
                f"обрезано {len(report.get('clipped') or [])} "
                f"ошибок {sum((report.get('errors') or {}).values())}"
                + (f" ОШИБКА {report['error'].get('code')}" if report.get("error") else "")
            )
    print("находки аудита (ошибки):", dict(total.most_common()))
    print("листы:", base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
