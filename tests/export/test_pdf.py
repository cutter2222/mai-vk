"""Конвертация в PDF: изоляция профиля, тайм-аут и очистка — на подменном soffice.

Настоящий LibreOffice проверяется отдельно, если он есть на машине (или в образе воркера).
"""

from __future__ import annotations

import os
import pathlib
import stat
import textwrap

import pytest

from presentation_designer.export import pdf

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "pptx" / "mini_template.pptx"


def _fake_soffice(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    script = tmp_path / "soffice"
    script.write_text("#!/bin/sh\n" + textwrap.dedent(body))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_convert_moves_pdf_and_cleans_profile(tmp_path: pathlib.Path) -> None:
    marker = tmp_path / "profile-seen"
    fake = _fake_soffice(
        tmp_path,
        f"""
        # аргументы: -env:UserInstallation=file://... --headless ... --outdir DIR FILE
        for a in "$@"; do
          case "$a" in
            -env:UserInstallation=*) echo "${{a#-env:UserInstallation=}}" > {marker};;
          esac
        done
        outdir=""; prev=""
        for a in "$@"; do if [ "$prev" = "--outdir" ]; then outdir="$a"; fi; prev="$a"; done
        mkdir -p "$outdir"
        name=$(basename "${{@: -1}}" .pptx)
        printf '%%PDF-1.4 fake' > "$outdir/$name.pdf"
        """,
    )
    out = tmp_path / "out"
    result = pdf.convert_to_pdf(
        FIXTURE, out, timeout_s=10, soffice=fake, use_profile_template=False
    )
    assert result.pdf_path == out / "mini_template.pdf"
    assert result.pdf_path.read_bytes().startswith(b"%PDF")
    assert result.seconds >= 0
    assert result.profile_from_template is False
    profile_uri = marker.read_text().strip()
    assert profile_uri.startswith("file://")
    assert not pathlib.Path(profile_uri.removeprefix("file://")).exists(), "профиль не удалён"


def test_convert_timeout_kills_process_tree(tmp_path: pathlib.Path) -> None:
    pid_file = tmp_path / "child.pid"
    fake = _fake_soffice(
        tmp_path,
        f"""
        sleep 30 &
        echo $! > {pid_file}
        wait
        """,
    )
    with pytest.raises(pdf.ConversionError, match="не уложился"):
        pdf.convert_to_pdf(FIXTURE, tmp_path / "out", timeout_s=1, soffice=fake)
    child = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)
    assert not (tmp_path / "out" / "mini_template.pdf").exists()


def test_convert_reports_failure(tmp_path: pathlib.Path) -> None:
    fake = _fake_soffice(tmp_path, "echo 'Error: source file could not be loaded' >&2; exit 1\n")
    with pytest.raises(pdf.ConversionError, match="код 1"):
        pdf.convert_to_pdf(FIXTURE, tmp_path / "out", timeout_s=5, soffice=fake)


def test_missing_input(tmp_path: pathlib.Path) -> None:
    fake = _fake_soffice(tmp_path, "exit 0\n")
    with pytest.raises(pdf.ConversionError, match="нет входного файла"):
        pdf.convert_to_pdf(tmp_path / "nope.pptx", tmp_path, soffice=fake)


def test_unavailable_renderer(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PD_SOFFICE", raising=False)
    monkeypatch.setattr(pdf, "find_soffice", lambda: None)
    with pytest.raises(pdf.RendererUnavailableError):
        pdf.convert_to_pdf(FIXTURE, tmp_path)


@pytest.mark.skipif(pdf.find_soffice() is None, reason="LibreOffice не установлен")
def test_real_libreoffice_converts_fixture(tmp_path: pathlib.Path) -> None:
    result = pdf.convert_to_pdf(FIXTURE, tmp_path, timeout_s=120, measure_memory=True)
    assert result.pdf_path.read_bytes().startswith(b"%PDF")
    assert result.max_rss_mb is None or result.max_rss_mb > 10
