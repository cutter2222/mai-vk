"""Live rendering check. Run in a worker with API/Document Server already available.

python scripts/check_onlyoffice.py /path/to/deck.pptx [...] --out /app/runs/onlyoffice-check
No LLM calls, editor sessions or changes to input artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3

from pptx import Presentation

from presentation_designer.export.deck import export_revision
from presentation_designer.export.thumbnails import pdf_page_count
from presentation_designer.parsing.template.analyzer import analyze_template
from presentation_designer.shared.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=pathlib.Path, nargs="+")
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    database = settings.paths.data_dir / "onlyoffice.sqlite3"

    def revisions() -> list[tuple[str, int, str]]:
        if not database.exists():
            return []
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
            return db.execute(
                "SELECT document_id, revision, sha256 FROM revisions ORDER BY document_id, revision"
            ).fetchall()

    office_before = revisions()
    report = []
    for index, source in enumerate(args.files):
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        expected = len(Presentation(str(source)).slides)
        out = args.out / f"{index:02d}-{source.stem}"
        out.mkdir(parents=True, exist_ok=True)
        composed_path = source.with_name("composed.json")
        composed = json.loads(composed_path.read_text()) if composed_path.exists() else None
        result = export_revision(
            source,
            out,
            prefix="",
            composed_deck=composed,
            deck_title=source.stem,
            settings=settings,
        )
        assert pdf_page_count(result.pdf_path) == expected
        assert len(result.thumbnails) == expected
        item = {"file": str(source), "pages": expected, "export": result.report}
        if args.analyze:
            analysis = analyze_template(
                source,
                template_id="tpl_onlyoffice_check",
                name=source.name,
                size_bytes=source.stat().st_size,
                settings=settings,
                render=True,
                use_vlm=False,
                workdir=out / "analysis",
            )
            assert analysis.report.renderer.startswith("onlyoffice")
            assert analysis.previews
            assert analysis.report.counts.get("layout_previews", 0) > 0
            item["analysis"] = {
                "previews": len(analysis.previews),
                "counts": analysis.report.counts,
            }
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        item["source_sha256_unchanged"] = before
        report.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    assert revisions() == office_before, "office revisions changed during rendering"
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("OK: page counts, thumbnails, input hashes and office revisions verified", flush=True)


if __name__ == "__main__":
    main()
