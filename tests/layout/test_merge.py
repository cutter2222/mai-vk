"""Слияние пакетов: id мастеров и макетов не сталкиваются, пакет остаётся целым."""

from __future__ import annotations

import collections
import pathlib

from pptx import Presentation

from presentation_designer.audit.deterministic import check_package
from presentation_designer.layout.merge import _SLD_LAYOUT_ID, merge_presentation


def _ids(prs) -> list[int]:
    lst = prs.part._element.get_or_add_sldMasterIdLst()
    ids = [int(e.get("id")) for e in lst.sldMasterId_lst]
    for master in prs.slide_masters:
        ids += [int(e.get("id")) for e in master.part._element.iter(_SLD_LAYOUT_ID)]
    return ids


def test_merged_masters_get_distinct_layout_ids(tmp_path: pathlib.Path) -> None:
    base = Presentation()
    source = Presentation()
    source.slides.add_slide(source.slide_layouts[1]).shapes.title.text = "Из второго файла"
    result = merge_presentation(base, source)
    assert result.masters == 1 and result.slides == 1
    assert len(base.slide_masters) == 2
    ids = _ids(base)
    assert [v for v in collections.Counter(ids).values() if v > 1] == []
    assert min(ids) >= 2147483648
    out = tmp_path / "merged.pptx"
    base.save(out)
    assert check_package(out) == []
