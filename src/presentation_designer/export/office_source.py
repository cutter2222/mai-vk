"""Ephemeral conversion inputs, separate from immutable editor revisions."""

from __future__ import annotations

import pathlib
import re
import shutil
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlencode, urlsplit

from presentation_designer.parsing.template.embedded_fonts import prepare_fonts
from presentation_designer.pipeline.office import sign
from presentation_designer.shared.log_redaction import configure_url_redaction
from presentation_designer.shared.settings import Settings

configure_url_redaction()


def source_path(settings: Settings, key: str) -> pathlib.Path:
    if not re.fullmatch(r"[a-f0-9]{32}", key):
        raise ValueError("invalid conversion key")
    return settings.paths.data_dir / "office-conversions" / f"{key}.pptx"


@contextmanager
def publish_source(path: pathlib.Path, settings: Settings, ttl: int) -> Iterator[tuple[str, str]]:
    prepare_fonts(path, settings.paths.data_dir)
    key = uuid.uuid4().hex
    target = source_path(settings, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Recover files left by killed workers (conversion budget is capped at one hour).
    for stale in target.parent.glob("*.pptx"):
        try:
            if stale.stat().st_mtime < time.time() - 7200:
                stale.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
    try:
        shutil.copyfile(path, target)
        token = sign(
            {"scope": "office-conversion", "key": key, "exp": int(time.time()) + ttl},
            settings.onlyoffice.jwt_secret,
        )
        url = settings.onlyoffice.storage_url.rstrip("/")
        yield key, f"{url}/api/office/conversions/{key}?{urlencode({'token': token})}"
    finally:
        target.unlink(missing_ok=True)


def saved_url(url: str, internal_url: str, public_url: str) -> str:
    """Allow only configured Document Server cache URLs; never follow redirects."""
    if not isinstance(url, str):
        raise ValueError("invalid save URL")
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("invalid save URL")
    for base in (internal_url, public_url):
        origin = urlsplit(base)
        prefix = origin.path.rstrip("/") + "/cache/files/"
        if (parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc):
            if (
                parsed.path.startswith(prefix)
                and ".." not in parsed.path
                and "%" not in parsed.path
            ):
                path = parsed.path[len(origin.path.rstrip("/")) :]
                return (
                    internal_url.rstrip("/") + path + (f"?{parsed.query}" if parsed.query else "")
                )
    raise ValueError("untrusted save URL")
