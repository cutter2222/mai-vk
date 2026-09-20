"""Сверка артефактов задания между собой: одна ли это колода во всех файлах.

Зачем: слайд показывается пользователю тремя разными путями — миниатюра и PDF от рендерера,
описание `composed.json` для редактора и рамок аудита, сам `deck.pptx` для скачивания. Если
они разойдутся, интерфейс покажет один слайд, а править и проверять будет другой, и по виду
это выглядит как «редактор путает слайды». Проверяются состав файлов, число слайдов, порядок
(по тексту), нумерация, ссылки аудита на объекты и рамки в пределах холста.

Запуск:
    uv run python tools/job_doctor.py                 # все задания в artifacts/jobs
    uv run python tools/job_doctor.py job_ab12…       # одно задание
    uv run python tools/job_doctor.py --verbose       # с построчной сверкой текста

Код возврата 1, если хоть одна проверка не прошла: годится для прогона в CI.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import zipfile
from dataclasses import dataclass, field
from typing import Any

JsonDict = dict[str, Any]

ROOT = pathlib.Path(__file__).resolve().parents[1]
JOBS = ROOT / "artifacts" / "jobs"
SLIDE_XML = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
TEXT_TAG = re.compile(r"<a:t>([^<]*)</a:t>")
WORD = re.compile(r"\w+", re.UNICODE)

# Доля общих слов, при которой считаем, что это один и тот же слайд. Ниже — расхождение:
# рендер и описание сделаны по разным колодам либо порядок слайдов не совпадает.
SAME_SLIDE_RATIO = 0.5
# Слайды без текста (только картинка или фигуры) сверять по словам нечем.
MIN_WORDS = 3


@dataclass
class Report:
    """Накопитель строк отчёта: что проверяли, что вышло."""

    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def ok(self, text: str) -> None:
        self.notes.append(f"  ✓ {text}")

    def bad(self, text: str) -> None:
        self.problems.append(text)
        self.notes.append(f"  ✗ {text}")

    def skip(self, text: str) -> None:
        self.notes.append(f"  · {text}")


def words(text: str) -> set[str]:
    return {w.lower() for w in WORD.findall(text) if len(w) > 2}


def pptx_slides_text(path: pathlib.Path) -> list[str]:
    """Текст слайдов в порядке презентации, а не в порядке имён частей."""
    with zipfile.ZipFile(path) as z:
        pres = z.read("ppt/presentation.xml").decode("utf-8")
        rels = z.read("ppt/_rels/presentation.xml.rels").decode("utf-8")
        order = re.findall(r'<p:sldId[^>]*r:id="(rId\d+)"', pres)
        targets = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', rels))
        out: list[str] = []
        for rid in order:
            target = targets.get(rid, "")
            name = "ppt/" + target.lstrip("/").removeprefix("ppt/")
            if name not in z.namelist():
                out.append("")
                continue
            out.append(" ".join(TEXT_TAG.findall(z.read(name).decode("utf-8"))))
    return out


def pdf_pages_text(path: pathlib.Path) -> list[str] | None:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    doc = pdfium.PdfDocument(str(path))
    return [page.get_textpage().get_text_range() for page in doc]


def composed_slides_text(deck: JsonDict) -> list[str]:
    out: list[str] = []
    for slide in deck.get("slides") or []:
        parts: list[str] = []
        for obj in slide.get("objects") or []:
            text = obj.get("text")
            if isinstance(text, dict) and isinstance(text.get("plain"), str):
                parts.append(text["plain"])
        out.append(" ".join(parts))
    return out


def similar(a: str, b: str) -> float | None:
    """Доля общих слов; None — сравнивать нечего."""
    wa, wb = words(a), words(b)
    if len(wa) < MIN_WORDS or len(wb) < MIN_WORDS:
        return None
    return len(wa & wb) / max(len(wa | wb), 1)


def check_order(rep: Report, name: str, left: list[str], right: list[str], verbose: bool) -> None:
    """Сверяет две последовательности слайдов по тексту и сообщает, где разошлись."""
    pairs = min(len(left), len(right))
    compared = 0
    mismatch: list[int] = []
    for i in range(pairs):
        ratio = similar(left[i], right[i])
        if ratio is None:
            continue
        compared += 1
        if ratio < SAME_SLIDE_RATIO:
            mismatch.append(i)
        if verbose:
            mark = "✓" if ratio >= SAME_SLIDE_RATIO else "✗"
            rep.skip(f"{name} слайд {i + 1}: совпадение {ratio:.0%} {mark}")
    if not compared:
        rep.skip(f"{name}: сравнивать нечего, текста нет")
        return
    if mismatch:
        shown = ", ".join(str(i + 1) for i in mismatch[:10])
        rep.bad(
            f"{name}: расходятся слайды {shown}"
            + (f" и ещё {len(mismatch) - 10}" if len(mismatch) > 10 else "")
            + f" (сверено {compared} из {pairs})"
        )
    else:
        rep.ok(f"{name}: порядок и содержание совпадают ({compared} слайдов сверено)")


def check_indices(rep: Report, deck: JsonDict) -> None:
    """Номера слайдов в описании должны идти подряд с нуля: по ним интерфейс ищет миниатюру."""
    slides = deck.get("slides") or []
    indices = [int(s.get("index", -1)) for s in slides]
    if indices != list(range(len(slides))):
        rep.bad(f"номера слайдов в описании не подряд: {indices[:12]}")
    else:
        rep.ok("номера слайдов в описании идут подряд с нуля")
    ids = [str(s.get("slide_id")) for s in slides]
    if len(set(ids)) != len(ids):
        rep.bad("идентификаторы слайдов повторяются")


def check_thumbs(rep: Report, manifest: JsonDict, r: pathlib.Path, slides: int) -> None:
    files = sorted(p.name for p in (r / "thumbs").glob("*.png"))
    if len(files) != slides:
        rep.bad(f"миниатюр {len(files)}, слайдов в описании {slides}")
    else:
        rep.ok(f"миниатюр столько же, сколько слайдов ({slides})")
    expected = [f"slide-{i + 1:02d}.png" for i in range(len(files))]
    if files != expected:
        rep.bad(f"имена миниатюр не подряд: {files[:5]}")
    missing = [
        n for n in manifest if n.endswith(".png") and not (ROOT / "artifacts" / "jobs").exists()
    ]
    if missing:
        rep.bad(f"в манифесте есть миниатюры без файлов: {missing[:3]}")


# У настоящего отчёта проверок столько же, сколько в реестре Приложения 1; короткий список —
# отчёт заглушки, где находки выдуманы фикстурой и на объекты колоды не ссылаются.
STUB_CHECKS_MAX = 10


def check_audit(rep: Report, audit: JsonDict, deck: JsonDict) -> None:
    """Каждая находка должна указывать на существующий слайд, объект и место на холсте."""
    if len(audit.get("checks") or []) < STUB_CHECKS_MAX:
        rep.skip(f"отчёт от заглушки аудита ({len(audit.get('checks') or [])} проверок), не сверяю")
        return
    by_index = {int(s.get("index", -1)): s for s in deck.get("slides") or []}
    objects = {
        (int(s.get("index", -1)), str(o.get("object_id"))): o
        for s in deck.get("slides") or []
        for o in s.get("objects") or []
    }
    bad_slide, bad_object, bad_box, shifted, empty_box = [], [], [], [], []
    for issue in audit.get("issues") or []:
        index = issue.get("slide_index")
        if index is None:
            continue
        if int(index) not in by_index:
            bad_slide.append(f"{issue['issue_id']} → слайд {index}")
            continue
        element_ids = [str(x) for x in issue.get("element_ids") or []]
        for object_id in element_ids:
            if objects.get((int(index), object_id)) is None:
                bad_object.append(f"{issue['issue_id']} → объект {object_id}")
        # Рамку сверяем только у находок об одном объекте: у наложения рамка — область
        # пересечения двух фигур и с их собственными рамками совпадать не обязана.
        if len(element_ids) == 1:
            obj = objects.get((int(index), element_ids[0]))
            box, ref = issue.get("bbox"), (obj or {}).get("bbox")
            if obj is not None and box and ref:
                far = max(abs(float(box[k]) - float(ref[k])) for k in ("x", "y", "width", "height"))
                if far > 0.02:
                    shifted.append(f"{issue['issue_id']} (на {far:.2f} холста)")
        box = issue.get("bbox")
        if not box:
            continue
        if float(box["width"]) <= 0.001 or float(box["height"]) <= 0.001:
            # Такую рамку пользователь не увидит: нулевая высота или ширина.
            empty_box.append(str(issue["issue_id"]))
        elif not (
            -0.001 <= float(box["x"]) <= 1.001
            and -0.001 <= float(box["y"]) <= 1.001
            and float(box["x"]) + float(box["width"]) <= 1.05
            and float(box["y"]) + float(box["height"]) <= 1.05
        ):
            bad_box.append(str(issue["issue_id"]))
    for name, items in (
        ("находки указывают на несуществующие слайды", bad_slide),
        ("находки указывают на объекты, которых нет на слайде", bad_object),
        ("рамки находок выходят за холст", bad_box),
        ("рамки находок нулевого размера, их не видно", empty_box),
        ("рамка находки не совпадает с рамкой объекта", shifted),
    ):
        if items:
            rep.bad(f"{name}: {len(items)} — {', '.join(items[:4])}")
    if not (bad_slide or bad_object or bad_box or shifted or empty_box):
        total = len(audit.get("issues") or [])
        rep.ok(f"находки ссылаются на существующие слайды и объекты ({total})")


def check_revision(rep: Report, r: pathlib.Path, verbose: bool) -> None:
    required = ["composed.json", "deck.pptx", "deck.pdf", "manifest.json"]
    missing = [n for n in required if not (r / n).is_file()]
    if len(missing) == len(required) - 1 and (r / "manifest.json").is_file():
        # В ревизии один манифест: вариант не собрался. Это видно в задании, а не здесь.
        rep.skip("вариант не собран: в ревизии только manifest.json")
        return
    if missing:
        rep.bad(f"нет файлов: {', '.join(missing)}")
        return
    deck = json.loads((r / "composed.json").read_text(encoding="utf-8"))
    manifest = json.loads((r / "manifest.json").read_text(encoding="utf-8"))
    described = composed_slides_text(deck)
    pptx = pptx_slides_text(r / "deck.pptx")
    pdf = pdf_pages_text(r / "deck.pdf")

    if len(described) != len(pptx):
        rep.bad(f"в описании {len(described)} слайдов, в pptx {len(pptx)}")
    else:
        rep.ok(f"в описании и в pptx одинаково слайдов ({len(pptx)})")
    if pdf is None:
        rep.skip("pypdfium2 не установлен, pdf не сверялся")
    elif len(pdf) != len(pptx):
        rep.bad(f"страниц в pdf {len(pdf)}, слайдов в pptx {len(pptx)}")
    else:
        rep.ok(f"страниц в pdf столько же, сколько слайдов ({len(pdf)})")

    check_indices(rep, deck)
    check_thumbs(rep, manifest, r, len(described))
    check_order(rep, "описание ↔ pptx", described, pptx, verbose)
    if pdf is not None:
        check_order(rep, "описание ↔ pdf (то, что видно)", described, pdf, verbose)

    audit_path = r / "audit.json"
    if audit_path.is_file():
        check_audit(rep, json.loads(audit_path.read_text(encoding="utf-8")), deck)
    else:
        rep.skip("audit.json нет: аудит не выполнялся")


def check_job(job_dir: pathlib.Path, verbose: bool) -> list[str]:
    problems: list[str] = []
    print(f"\n=== {job_dir.name} ===")
    for variant in sorted(p for p in job_dir.iterdir() if p.is_dir()):
        for r in sorted(p for p in variant.iterdir() if p.is_dir() and p.name.startswith("r")):
            rep = Report()
            check_revision(rep, r, verbose)
            print(f"{variant.name}/{r.name}:")
            for line in rep.notes:
                print(line)
            problems += [f"{job_dir.name} {variant.name}/{r.name}: {p}" for p in rep.problems]
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сверка артефактов задания между собой")
    parser.add_argument("jobs", nargs="*", help="идентификаторы заданий; по умолчанию все")
    parser.add_argument("--verbose", action="store_true", help="построчная сверка текста слайдов")
    args = parser.parse_args(argv)

    if not JOBS.is_dir():
        print(f"нет каталога {JOBS}")
        return 2
    names = args.jobs or sorted(p.name for p in JOBS.iterdir() if p.is_dir())
    problems: list[str] = []
    for name in names:
        job_dir = JOBS / name
        if not job_dir.is_dir():
            print(f"задания {name} нет")
            problems.append(f"{name}: каталога нет")
            continue
        problems += check_job(job_dir, args.verbose)

    print("\n=== итог ===")
    if problems:
        print(f"расхождений: {len(problems)}")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print(f"заданий проверено: {len(names)}, расхождений нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
