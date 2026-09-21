"""PPTX → PDF through ONLYOFFICE; bounded polling, signed inputs, atomic output.

Concurrency is bounded by the caller's render slots. Editor sessions/revisions are
not involved. API and workers must share settings.paths.data_dir.
"""

from __future__ import annotations

import pathlib
import tempfile
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

import httpx
import pypdfium2 as pdfium

from presentation_designer.export.office_source import publish_source, saved_url
from presentation_designer.export.render_slots import RenderSlotTimeoutError, render_slots_from_env
from presentation_designer.export.thumbnails import pdf_page_count
from presentation_designer.pipeline.office import sign
from presentation_designer.shared.settings import Settings, get_settings


class RendererUnavailableError(RuntimeError):
    """ONLYOFFICE is unconfigured or cannot be reached."""


class ConversionError(RuntimeError):
    """Conversion failed, timed out or returned an invalid PDF."""


@dataclass(frozen=True)
class PdfResult:
    pdf_path: pathlib.Path
    seconds: float


def renderer_configured(settings: Settings | None = None) -> bool:
    cfg = (settings or get_settings()).onlyoffice
    return cfg.enabled and len(cfg.jwt_secret) >= 32


def convert_to_pdf(
    pptx_path: pathlib.Path,
    out_dir: pathlib.Path,
    timeout_s: int = 90,
    *,
    settings: Settings | None = None,
    slot_acquired: bool = False,
) -> PdfResult:
    """Convert without modifying the source; publish the PDF only after validation."""
    settings = settings or get_settings()
    cfg = settings.onlyoffice
    if not renderer_configured(settings):
        raise RendererUnavailableError(
            "ONLYOFFICE не настроен: включите сервис и задайте JWT secret"
        )
    if not 1 <= timeout_s <= 3600:
        raise ValueError("conversion timeout must be between 1 and 3600 seconds")
    pptx_path, out_dir = pathlib.Path(pptx_path), pathlib.Path(out_dir)
    if not pptx_path.is_file():
        raise ConversionError(f"нет входного файла: {pptx_path}")
    if pptx_path.stat().st_size > settings.limits.max_upload_mb * 1024 * 1024:
        raise ConversionError("PPTX превышает лимит конвертации")
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    deadline = started + timeout_s

    def remaining() -> float:
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise ConversionError(f"ONLYOFFICE не уложился в {timeout_s} с")
        return min(budget, 30.0)

    try:
        with (
            nullcontext()
            if slot_acquired
            else render_slots_from_env(settings.render.slots).acquire(
                timeout_s=timeout_s,
                ttl_s=timeout_s + 60,
            ),
            publish_source(pptx_path, settings, timeout_s + 60) as (key, url),
            httpx.Client(follow_redirects=False, trust_env=False) as client,
            tempfile.TemporaryDirectory(prefix=".office-pdf-", dir=out_dir) as tmp,
        ):
            payload: dict[str, Any] = {
                "async": True,
                "filetype": "pptx",
                "outputtype": "pdf",
                "key": key,
                "url": url,
                "title": "deck.pptx",
            }
            body = {**payload, "token": sign(payload, cfg.jwt_secret)}
            while True:
                response = client.post(
                    cfg.internal_url.rstrip("/") + "/converter",
                    params={"shardkey": key},
                    json=body,
                    headers={"Accept": "application/json"},
                    timeout=remaining(),
                )
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise ConversionError("Некорректный ответ конвертера ONLYOFFICE")
                if result.get("error"):
                    raise ConversionError(f"ONLYOFFICE: ошибка конвертации {result['error']}")
                if result.get("endConvert") is True:
                    break
                time.sleep(min(0.5, remaining()))
            download = saved_url(result["fileUrl"], cfg.internal_url, cfg.public_url)
            produced = pathlib.Path(tmp) / "output.pdf"
            size = 0
            with client.stream("GET", download, timeout=remaining()) as response:
                response.raise_for_status()
                with produced.open("wb") as output:
                    for chunk in response.iter_bytes(chunk_size=65536):
                        remaining()
                        size += len(chunk)
                        if size > cfg.max_pdf_mb * 1024 * 1024:
                            raise ConversionError("PDF превышает лимит конвертации")
                        output.write(chunk)
            with produced.open("rb") as content:
                if content.read(5) != b"%PDF-":
                    raise ConversionError("ONLYOFFICE вернул не PDF")
            try:
                if pdf_page_count(produced) == 0:
                    raise ConversionError("ONLYOFFICE вернул пустой PDF")
            except pdfium.PdfiumError as exc:
                raise ConversionError("ONLYOFFICE вернул повреждённый PDF") from exc
            remaining()
            final = out_dir / f"{pptx_path.stem}.pdf"
            produced.replace(final)
    except RenderSlotTimeoutError as exc:
        raise ConversionError("Не удалось дождаться слота конвертации ONLYOFFICE") from exc
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RendererUnavailableError("ONLYOFFICE недоступен; проверьте Document Server") from exc
    except httpx.TimeoutException as exc:
        raise ConversionError(f"Тайм-аут ONLYOFFICE ({timeout_s} с)") from exc
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise ConversionError("Некорректный ответ ONLYOFFICE при конвертации PDF") from exc
    return PdfResult(final, time.monotonic() - started)
