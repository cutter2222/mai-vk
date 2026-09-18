"""Стенд одного слайда: правка → сборка → замер за секунды, без модели.

Полный прогон колоды стоит около трёх минут: смысловой план, три варианта,
вёрстка и экспорт. Для работы над слоем `design` это дорого — правишь одну
строку и ждёшь. Стенд берёт **уже посчитанные** план, профиль и пакет из
последнего задания и пересобирает только нужные слайды.

Модель не вызывается вовсе, если не просить `--refill`: план уже построен, а
слой `design` детерминированный.

    python3 tools/slide_lab.py 9              один слайд
    python3 tools/slide_lab.py 9 10 --png     со снимками
    python3 tools/slide_lab.py --all --raw    вся колода без слоя design
    python3 tools/slide_lab.py --all --facts  что вёрстка сделала: пустоты и переполнения
    python3 tools/slide_lab.py --all --polish правка по фактам (с --ask — с моделью)

Запускать внутри контейнера воркера:

    docker compose ... exec worker-generation python /app/tools/slide_lab.py 9
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
import time

sys.path.insert(0, "/app/src")

from presentation_designer import design
from presentation_designer.layout.compose import compose_deck
from presentation_designer.shared.settings import get_settings

DB = pathlib.Path("/app/data/state.sqlite3")
ARTIFACTS = pathlib.Path("/app/artifacts")
OUT = pathlib.Path("/app/artifacts/slide-lab")


def _latest_job(variant: str) -> pathlib.Path:
    """Последнее задание, у которого есть готовый план варианта."""
    plans = sorted(
        ARTIFACTS.glob(f"jobs/*/{variant}/r1/plan.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not plans:
        raise SystemExit("нет ни одного посчитанного плана: сначала обычная генерация")
    return plans[0]


def _from_db(table: str, column: str, where: str = "") -> dict:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        f"select {column} from {table} {where} order by created_at desc limit 1"
    ).fetchone()
    if row is None or row[0] is None:
        raise SystemExit(f"в {table} нет записи со столбцом {column}")
    return json.loads(row[0])


def _template_file(sha256: str) -> pathlib.Path:
    """Загруженный файл шаблона: хранилище раскладывает по первым байтам хэша."""
    uploads = pathlib.Path(get_settings().paths.data_dir) / "uploads"
    for path in uploads.rglob("*"):
        if path.is_file() and sha256.startswith(path.parent.name):
            return path
    matches = [p for p in uploads.rglob("*") if p.is_file()]
    if not matches:
        raise SystemExit("файл шаблона не найден в хранилище")
    return max(matches, key=lambda p: p.stat().st_size)


def _model_ask():
    """Вызов модели через скилл `slot_filler` — как в конвейере."""
    from presentation_designer.pipeline.real import RealLayers

    layers = RealLayers(get_settings())
    client, skill = layers.llm_client(), layers.skill("slot_filler")
    if client is None or skill is None:
        raise SystemExit("модель не настроена: дозапрос невозможен")

    def ask(_system: str, user: str) -> str:
        return client.complete_sync(skill.request("fill.slots", user, stage="plan")).text

    return ask


def main() -> int:
    ap = argparse.ArgumentParser(description="Пересборка отдельных слайдов")
    ap.add_argument("slides", nargs="*", type=int, help="номера слайдов (с единицы)")
    ap.add_argument("--all", action="store_true", help="вся колода")
    ap.add_argument("--variant", default="balanced")
    ap.add_argument("--raw", action="store_true", help="без слоя design — как отдала модель")
    ap.add_argument("--png", action="store_true", help="снимки слайдов рядом с .pptx")
    ap.add_argument("--facts", action="store_true", help="факты собранного файла и счёт дефектов")
    ap.add_argument("--polish", action="store_true",
                    help="правка по фактам: сборка → факты → правка")
    ap.add_argument("--ask", action="store_true", help="разрешить дозапрос к модели")
    args = ap.parse_args()

    if not args.slides and not args.all:
        ap.error("укажите номера слайдов или --all")

    started = time.perf_counter()
    plan_path = _latest_job(args.variant)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    job = plan_path.parent.parent.parent.name
    print(f"план: {job}/{args.variant}, слайдов {len(plan['slides'])}")

    template = _from_db("templates", "profile", "where status='succeeded'")
    package = _from_db("packages", "package", "where status='succeeded'")
    sha = sqlite3.connect(DB).execute(
        "select sha256 from templates where status='succeeded' order by created_at desc limit 1"
    ).fetchone()[0]

    if not args.raw:
        story = plan.get("story") or {}
        plan, report = design.apply(plan, template, story, variant=args.variant)
        print(f"слой design: {report.summary()}")
        for decision in report.decisions:
            if decision.action != "keep":
                print(f"   {decision.slide_id:5} {decision.action:18} {decision.reason[:70]}")

    if not args.all:
        wanted = set(args.slides)
        plan = {**plan, "slides": [s for i, s in enumerate(plan["slides"], 1) if i in wanted]}
        if not plan["slides"]:
            raise SystemExit(f"в плане нет слайдов {sorted(wanted)}")
        print(f"собираю слайды: {sorted(wanted)}")

    OUT.mkdir(parents=True, exist_ok=True)
    name = "-".join(map(str, args.slides)) or "all"
    out_pptx = OUT / f"{args.variant}-{name}.pptx"
    template_file = _template_file(sha)

    def build(doc: dict, path: pathlib.Path = out_pptx):
        return compose_deck(doc, template, template_file, package,
                            out_pptx=path, job_id="slide_lab", variant_id=args.variant)

    if args.polish:
        ask = _model_ask() if args.ask else None
        plan, report = design.polish(
            plan, template, plan.get("story") or {},
            compose=lambda doc: build(doc, OUT / "draft.pptx").deck,
            variant=args.variant, ask=ask, package=package,
        )
        brief = {k: v for k, v in report.items() if k not in ("decisions", "refilled")}
        print("правка по фактам:", json.dumps(brief, ensure_ascii=False))
        for decision in report.get("decisions", []):
            print(f"   {decision['slide_id']:5} {decision['action']:14} {decision['reason'][:80]}")
        for slide_id, slot_id in report.get("refilled", []):
            print(f"   {slide_id:5} дозапрос      {slot_id}")
        saved = OUT / f"{args.variant}-polished.plan.json"
        saved.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"исправленный план: {saved}")

    result = build(plan)

    if args.facts or args.polish:
        from presentation_designer.design import feedback

        facts = feedback.read(result.deck, plan, template)
        print(f"\nфакты вёрстки: {json.dumps(facts.summary(), ensure_ascii=False)}")
        for fact in facts.items:
            place = f"{fact.slide_index + 1:>3} {fact.slide_id:5} {fact.slot_id:10}"
            if fact.kind in feedback.EMPTY_KINDS:
                print(f"   {place} {fact.kind:8} пусто, вмещает {fact.capacity_chars} знаков")
            else:
                print(f"   {place} {fact.kind:8} строк {fact.needed_lines} при {fact.fits_lines}"
                      f": {fact.text[:40]!r}")
    print(f"\nсобрано: {out_pptx}  за {time.perf_counter() - started:.1f} с")
    warnings = (result.report or {}).get("warnings") or []
    if warnings:
        print(f"предупреждений вёрстки: {len(warnings)}")
        for w in warnings[:5]:
            print("   ", json.dumps(w, ensure_ascii=False)[:110])

    if args.png:
        import subprocess

        subprocess.run(
            ["soffice", "--headless", "--convert-to", "png", "--outdir", str(OUT), str(out_pptx)],
            check=False, capture_output=True, timeout=120,
        )
        print(f"снимки: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
