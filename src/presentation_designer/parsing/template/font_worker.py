"""Resource-bounded font decoder. Invoked in a disposable subprocess, never in the API.

This is process/resource isolation, not a security sandbox for native code. Deploy
workers unprivileged with container isolation; never run this module as root.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import json
import pathlib
import resource
import struct
import subprocess
import sys
from typing import Any

MAX_FONT = 16 * 1024 * 1024


def inspect_font(data: bytes) -> dict[str, Any]:
    from fontTools.ttLib import TTFont

    if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        raise ValueError("decoded file is not a standalone TTF/OTF")
    if len(data) < 12:
        raise ValueError("truncated sfnt")
    count = struct.unpack_from(">H", data, 4)[0]
    if not 1 <= count <= 256 or 12 + count * 16 > len(data):
        raise ValueError("invalid sfnt directory")
    tags = set()
    spans = []
    for index in range(count):
        tag, _, offset, length = struct.unpack_from(">4sIII", data, 12 + index * 16)
        if tag in tags or offset + length > len(data) or offset < 12 + count * 16:
            raise ValueError("invalid sfnt table")
        tags.add(tag)
        if length:
            spans.append((offset, offset + length))
    spans.sort()
    if any(a[1] > b[0] for a, b in itertools.pairwise(spans)):
        raise ValueError("overlapping sfnt tables")
    with TTFont(io.BytesIO(data), lazy=False) as font:
        font.ensureDecompiled()
        for required in ("head", "hhea", "hmtx", "maxp", "name", "OS/2", "cmap"):
            if required not in font:
                raise ValueError(f"missing table {required}")
        if not any(tag in font for tag in ("glyf", "CFF ", "CFF2")):
            raise ValueError("missing outlines")
        names = font["name"]
        family = names.getDebugName(16) or names.getDebugName(1)
        style = names.getDebugName(17) or names.getDebugName(2)
        cmap = font.getBestCmap() or {}
        if not family or not style or not cmap or not font["maxp"].numGlyphs:
            raise ValueError("missing names or Unicode coverage")
        return {
            "family": family,
            "style": style,
            "version": names.getDebugName(5),
            "fs_type": int(font["OS/2"].fsType),
            "bold": bool(font["head"].macStyle & 1),
            "italic": bool(font["head"].macStyle & 2),
            "glyph_count": font["maxp"].numGlyphs,
            "codepoints": sorted(cmap),
            "sha256": hashlib.sha256(data).hexdigest(),
            "format": "otf" if data[:4] == b"OTTO" else "ttf",
        }


def decode(source: pathlib.Path, decoder: str) -> dict[str, Any]:
    data = source.read_bytes()
    if len(data) > MAX_FONT:
        return {"status": "size_limit"}
    signature = data[:4]
    extra: dict[str, Any] = {"source_format": "otf" if signature == b"OTTO" else "ttf"}
    if signature not in (b"\x00\x01\x00\x00", b"OTTO", b"true") and (
        len(data) >= 36 and data[34:36] == b"LP"
    ):
        total, size, version, flags = struct.unpack_from("<IIII", data)
        extra = {"source_format": "eot", "eot_version": version, "eot_flags": flags}
        if total != len(data) or not 0 < size <= total - 82:
            return {**extra, "status": "invalid_eot"}
        if version not in (0x10000, 0x20001, 0x20002) or flags & ~0x100000F5:
            return {**extra, "status": "unsupported_eot"}
        permissions = struct.unpack_from("<H", data, 32)[0]
        extra["eot_fs_type"] = permissions
        extra["subset"] = bool(flags & 1)
        # Preview/print-only fonts cannot be promoted for an editable document.
        if permissions & 0x202 or (permissions & 4 and not permissions & 8):
            return {**extra, "status": "embedding_restricted"}
        if not decoder:
            return {**extra, "status": "decoder_unavailable"}
        output = source.with_suffix(".decoded")
        result = subprocess.run(
            [decoder, str(source), str(output)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode or not output.is_file() or output.stat().st_size > MAX_FONT:
            return {**extra, "status": "decode_failed"}
        data = output.read_bytes()
    elif signature in (b"ttcf", b"wOFF", b"wOF2"):
        return {
            "status": "unsupported_format",
            "source_format": {
                b"ttcf": "collection",
                b"wOFF": "woff",
                b"wOF2": "woff2",
            }[signature],
        }
    elif signature not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        return {"status": "unsupported_format", "source_format": "unknown"}
    info = inspect_font(data)
    permissions = info["fs_type"]
    if permissions & 0x202 or (permissions & 4 and not permissions & 8):
        return {**extra, **info, "status": "embedding_restricted"}
    source.with_suffix(".font").write_bytes(data)
    return {**extra, **info, "status": "validated"}


def main() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FONT, MAX_FONT))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    source = pathlib.Path(sys.argv[1])
    try:
        result = decode(source, sys.argv[2])
    except Exception:
        result = {"status": "invalid_font"}
    source.with_suffix(".json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
