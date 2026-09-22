"""Strict callback validation and a narrowly scoped ONLYOFFICE matchingName workaround.

Never use XML recovery. Only escape an exact raw name already present in the valid
session source, only in the root slide-layout attribute, and validate every part
afterward. Callers must retain the raw bytes and expose the normalization audit.
"""

from __future__ import annotations

import io
import re
from zipfile import BadZipFile, ZipFile

from lxml import etree

P = "http://schemas.openxmlformats.org/presentationml/2006/main"


def _parse(data: bytes) -> etree._Element:
    root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
    if root.getroottree().docinfo.doctype:
        raise ValueError("DTD is not permitted in OOXML")
    return root


def validate_saved_pptx(data: bytes, source: bytes) -> tuple[bytes, list[str]]:
    try:
        with ZipFile(io.BytesIO(source)) as original, ZipFile(io.BytesIO(data)) as saved:
            if len(set(saved.namelist())) != len(saved.namelist()):
                raise ValueError("Duplicate OOXML parts")
            names = set()
            for name in original.namelist():
                if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", name):
                    value = _parse(original.read(name)).get("matchingName")
                    if value:
                        names.add(value)
            replacements = {}
            for part in saved.namelist():
                if not part.endswith((".xml", ".rels")):
                    continue
                xml = saved.read(part)
                try:
                    _parse(xml)
                    continue
                except etree.XMLSyntaxError:
                    pass
                if not re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", part):
                    raise ValueError(f"Invalid OOXML: {part}")
                # No arbitrary malformed-XML regex repair: match the exact source
                # value at the root, and require a single unambiguous valid result.
                candidates = set()
                for value in names:
                    raw = b' matchingName="' + value.encode() + b'"'
                    escaped = (
                        value.replace("&", "&amp;")
                        .replace('"', "&quot;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                        .replace("\t", "&#9;")
                        .replace("\n", "&#10;")
                        .replace("\r", "&#13;")
                    )
                    prefix = xml.find(b"<p:sldLayout ")
                    position = xml.find(raw, prefix) if prefix >= 0 else -1
                    if position < 0 or b">" in xml[prefix:position]:
                        continue
                    fixed = xml[:position] + b' matchingName="' + escaped.encode() + b'"'
                    fixed += xml[position + len(raw) :]
                    try:
                        root = _parse(fixed)
                    except (etree.XMLSyntaxError, ValueError):
                        continue
                    if root.tag == f"{{{P}}}sldLayout" and root.get("matchingName") == value:
                        candidates.add(fixed)
                if len(candidates) != 1:
                    raise ValueError(f"Invalid OOXML: {part}")
                replacements[part] = candidates.pop()
            if not replacements:
                return data, []
            output = io.BytesIO()
            with ZipFile(output, "w") as archive:
                archive.comment = saved.comment
                for info in saved.infolist():
                    archive.writestr(info, replacements.get(info.filename, saved.read(info)))
            return output.getvalue(), sorted(replacements)
    except (BadZipFile, etree.XMLSyntaxError) as exc:
        raise ValueError("Invalid OOXML archive") from exc
