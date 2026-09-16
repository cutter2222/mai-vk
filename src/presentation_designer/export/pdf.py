"""Конвертация PPTX в PDF через LibreOffice: отдельный процесс на конвертацию.

Каждый вызов получает собственный профиль (`UserInstallation`) во временном каталоге,
тайм-аут и завершение всего дерева процессов (oosplash → soffice.bin). Подготовленный
профиль из образа воркера (`PD_LO_PROFILE_TEMPLATE`, по умолчанию /opt/lo-profile)
копируется в этот каталог: так первый запуск не тратит время на инициализацию реестра.
Ограничение одновременных конвертаций (слоты рендера) — задача вызывающего слоя.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

DEFAULT_PROFILE_TEMPLATE = pathlib.Path("/opt/lo-profile")
_MEMORY_WRAPPER = (
    "import resource, subprocess, sys\n"
    "code = subprocess.call(sys.argv[1:])\n"
    "print('max_rss_kb=%d' % resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss, "
    "file=sys.stderr)\n"
    "sys.exit(code)\n"
)


class RendererUnavailableError(RuntimeError):
    """LibreOffice не найден в PATH."""


class ConversionError(RuntimeError):
    """Конвертация завершилась ошибкой, тайм-аутом или без выходного файла."""


@dataclass(frozen=True)
class PdfResult:
    pdf_path: pathlib.Path
    seconds: float
    profile_from_template: bool
    max_rss_mb: float | None = None  # пиковая память процессов LibreOffice, если измерялась


def find_soffice() -> pathlib.Path | None:
    """Путь к LibreOffice: PD_SOFFICE, PATH или стандартное место на macOS."""
    explicit = os.environ.get("PD_SOFFICE")
    if explicit:
        return pathlib.Path(explicit)
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return pathlib.Path(found)
    mac = pathlib.Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    return mac if mac.exists() else None


def soffice_version(soffice: pathlib.Path | None = None) -> str | None:
    soffice = soffice or find_soffice()
    if soffice is None:
        return None
    try:
        out = subprocess.run(
            [str(soffice), "--version"], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip().splitlines()[0] if out.stdout.strip() else None


def profile_template_dir() -> pathlib.Path | None:
    """Подготовленный профиль LibreOffice, если он есть на этой машине."""
    raw = os.environ.get("PD_LO_PROFILE_TEMPLATE")
    path = pathlib.Path(raw) if raw else DEFAULT_PROFILE_TEMPLATE
    return path if (path / "user").is_dir() else None


def convert_to_pdf(
    pptx_path: pathlib.Path,
    out_dir: pathlib.Path,
    timeout_s: int = 90,
    soffice: pathlib.Path | None = None,
    use_profile_template: bool = True,
    measure_memory: bool = False,
) -> PdfResult:
    """Конвертирует один PPTX в `out_dir/<имя>.pdf`.

    Временный каталог с профилем удаляется всегда; при тайм-ауте дерево процессов
    получает SIGKILL. Выходной PDF сначала пишется во временный каталог и переносится
    в `out_dir` только целиком.
    """
    soffice = soffice or find_soffice()
    if soffice is None:
        raise RendererUnavailableError(
            "LibreOffice не найден: задайте PD_SOFFICE или добавьте soffice в PATH"
        )
    pptx_path = pathlib.Path(pptx_path)
    if not pptx_path.is_file():
        raise ConversionError(f"нет входного файла: {pptx_path}")
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    template = profile_template_dir() if use_profile_template else None
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="pd-lo-") as tmp:
        work = pathlib.Path(tmp)
        profile = work / "profile"
        if template is not None:
            shutil.copytree(template, profile, symlinks=True)
        else:
            profile.mkdir()
        # LibreOffice принимает UserInstallation только как file:// URL; путь с пробелами
        # и кириллицей без экранирования в нём не работает, поэтому профиль — временный.
        cmd = [
            str(soffice),
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--norestore",
            "--nologo",
            "--nolockcheck",
            "--convert-to",
            "pdf:impress_pdf_Export",
            "--outdir",
            str(work / "out"),
            str(pptx_path),
        ]
        if measure_memory:
            cmd = [sys.executable, "-c", _MEMORY_WRAPPER, *cmd]
        env = dict(os.environ, HOME=str(work), TMPDIR=str(work))
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            raise ConversionError(
                f"LibreOffice не уложился в {timeout_s} с для {pptx_path.name}"
            ) from None
        seconds = time.perf_counter() - started
        produced = work / "out" / f"{pptx_path.stem}.pdf"
        if proc.returncode != 0 or not produced.is_file():
            tail = (stderr or stdout).decode("utf-8", "replace").strip()[-800:]
            raise ConversionError(
                f"LibreOffice вернул код {proc.returncode} для {pptx_path.name}: {tail}"
            )
        max_rss_mb = _parse_max_rss(stderr) if measure_memory else None
        final = out_dir / produced.name
        shutil.move(str(produced), final)
    return PdfResult(final, seconds, template is not None, max_rss_mb)


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _parse_max_rss(stderr: bytes) -> float | None:
    for line in stderr.decode("utf-8", "replace").splitlines():
        if line.startswith("max_rss_kb="):
            kb = int(line.split("=", 1)[1])
            # ru_maxrss в килобайтах на Linux и в байтах на macOS.
            return round(kb / 1024, 1) if sys.platform != "darwin" else round(kb / 1024 / 1024, 1)
    return None
