"""Контракт операций чата (этап 36, `contracts/schemas/chat_ops.schema.json`).

Одна схема на оба мира: план варианта и офисную копию. Здесь — проверка документа по схеме,
проверка адресов по снимку колоды (`generation/snapshot.py`) и перевод `overrides` плана в
операции и обратно: overrides — подмножество контракта, поэтому исполнитель плана уже есть
для текста, стиля, геометрии, картинки, фона, удаления и своей надписи. Исполнителей новых
операций и роутера здесь нет (этапы 37–44).
"""

from __future__ import annotations

import copy
import json
import pathlib
from functools import lru_cache
from typing import Any

JsonDict = dict[str, Any]

SCHEMAS = pathlib.Path(__file__).resolve().parents[3] / "contracts" / "schemas"
# Свойство шрифта правки style → операция и имя её аргумента.
STYLE_OPS = {
    "family": ("style.font", "family"),
    "size_pt": ("style.size", "size_pt"),
    "bold": ("style.bold", "value"),
    "italic": ("style.italic", "value"),
    "color": ("style.color", "color"),
}
STYLE_KEYS = {name: (key, arg) for key, (name, arg) in STYLE_OPS.items()}


@lru_cache(maxsize=1)
def _validator() -> Any:
    import jsonschema
    from referencing import Registry, Resource

    registry = Registry()
    target = None
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
        if path.name == "chat_ops.schema.json":
            target = schema
    if target is None:
        raise FileNotFoundError(f"нет схемы chat_ops в {SCHEMAS}")
    return jsonschema.Draft202012Validator(target, registry=registry)


def validate(doc: JsonDict) -> None:
    """Документ по схеме `chat_ops`; ошибка — ValueError с первыми нарушениями."""
    errors = sorted(_validator().iter_errors(doc), key=lambda e: list(e.path))
    if errors:
        raise ValueError(
            "Операции не по схеме: "
            + "; ".join(
                f"{'/'.join(str(p) for p in e.path) or 'документ'}: {e.message[:160]}"
                for e in errors[:5]
            )
        )


def check_addresses(doc: JsonDict, snapshot: JsonDict) -> list[str]:
    """Адреса операций по снимку ревизии `base`: чего нет — человеческой фразой. Пустой список —
    все адреса на месте. Новые объекты (object.add_text этого же документа) адресуемы."""
    slides = snapshot.get("slides") or []
    created = {
        str(op["args"]["new_object_id"])
        for op in doc.get("ops") or []
        if op.get("op") == "object.add_text" and (op.get("args") or {}).get("new_object_id")
    }
    problems: list[str] = []
    for number, op in enumerate(doc.get("ops") or [], 1):
        name = f"операция {number} ({op.get('op')})"
        address = op.get("address") or {}
        refs = [address["slide"]] if address.get("slide") else list(address.get("slides") or [])
        found = []
        for ref in refs:
            slide = _find_slide(slides, ref)
            if slide is None:
                problems.append(f"{name}: нет слайда {_slide_label(ref)}")
            else:
                found.append(slide)
        if not found or len(found) != len(refs):
            continue
        slide = found[0]
        objects = [address["object"]] if address.get("object") else []
        objects += list(address.get("objects") or [])
        for ref in objects:
            if str(ref.get("object_id")) in created:
                continue
            obj = _find_object(slide, ref)
            if obj is None:
                problems.append(
                    f"{name}: на слайде {slide['index']} нет объекта {ref.get('object_id')}"
                )
                continue
            paragraph = address.get("paragraph") or (address.get("fragment") or {}).get("paragraph")
            count = len((obj.get("text") or {}).get("paragraphs") or [])
            if paragraph and paragraph > count:
                problems.append(
                    f"{name}: в объекте {ref.get('object_id')} {count} абзацев, "
                    f"абзаца {paragraph} нет"
                )
            fragment = (address.get("fragment") or {}).get("text")
            if fragment and fragment not in str((obj.get("text") or {}).get("plain", "")):
                problems.append(f"{name}: в объекте {ref.get('object_id')} нет «{fragment}»")
            cell = address.get("cell")
            if cell:
                rows = (obj.get("table") or {}).get("rows")
                if rows is None:
                    problems.append(f"{name}: объект {ref.get('object_id')} — не таблица")
                elif cell["row"] > len(rows) or cell["col"] > len(rows[cell["row"] - 1]):
                    problems.append(f"{name}: в таблице нет ячейки {cell['row']}×{cell['col']}")
    return problems


def _find_slide(slides: list[JsonDict], ref: JsonDict) -> JsonDict | None:
    for slide in slides:
        if ref.get("slide_id") and slide.get("slide_id") != ref["slide_id"]:
            continue
        if ref.get("index") and slide["index"] != ref["index"]:
            continue
        if ref.get("sld_id") is not None and slide["sld_id"] != ref["sld_id"]:
            continue
        return slide
    return None


def _slide_label(ref: JsonDict) -> str:
    return str(ref.get("index") or ref.get("slide_id") or ref.get("sld_id"))


def _find_object(slide: JsonDict, ref: JsonDict) -> JsonDict | None:
    path = list(ref.get("group_path") or [])
    obj: JsonDict
    for obj in slide.get("objects") or []:
        address = obj["address"]
        if address["object_id"] != str(ref.get("object_id")):
            continue
        if "group_path" in ref and list(address.get("group_path") or []) != path:
            continue
        return obj
    return None


# ---------- overrides плана ⇄ операции ----------


def _object_ref(target: JsonDict) -> JsonDict:
    return {
        key: str(target[key])
        for key in ("object_id", "slot_id", "source_object_id")
        if target.get(key)
    }


def _op(name: str, address: JsonDict, args: JsonDict, source: str = "snapshot") -> JsonDict:
    return {"op": name, "address": address, "source": source, "confirm": False, "args": args}


def ops_from_override(override: JsonDict, slide: JsonDict) -> list[JsonDict]:
    """Операции, равные одной правке плана. `slide` — ссылка на слайд (`slide_id` плана)."""
    kind = override["op"]
    target = override.get("target") or {}
    at_object = {"scope": "object", "slide": slide, "object": _object_ref(target)}
    if kind == "text":
        text = str(override["text"])
        return [_op("text.set", at_object, {"text": text}, _text_source(text))]
    if kind == "style":
        style = override.get("style") or {}
        font = style.get("font") or {}
        out = [
            _op(name, at_object, {arg: font[key]}, "template")
            for key, (name, arg) in STYLE_OPS.items()
            if key in font
        ]
        if "align" in style:
            out.append(_op("style.align", at_object, {"align": style["align"]}, "template"))
        return out
    if kind == "geometry":
        box = override["geometry"]["bbox"]
        return [
            _op("object.move", at_object, {"x": box["x"], "y": box["y"]}),
            _op("object.resize", at_object, {"width": box["width"], "height": box["height"]}),
        ]
    if kind == "picture":
        picture = override["picture"]
        origin = {"template": "template", "package": "package"}.get(
            str(picture["source"].get("kind")), "attachment"
        )
        replace: JsonDict = {"asset": copy.deepcopy(picture["source"])}
        if picture.get("fit"):
            replace["fit"] = picture["fit"]
        out = [_op("picture.replace", at_object, replace, origin)]
        if picture.get("color"):
            out.append(_op("picture.recolor", at_object, {"color": picture["color"]}, "template"))
        return out
    if kind == "background":
        background = override["background"]
        at_slide = {"scope": "slide", "slide": slide}
        if background["kind"] == "solid":
            color = {"color": background["color"]} if background.get("color") else {}
            return [_op("background.solid", at_slide, color, "template")]
        if background["kind"] == "image":
            image: JsonDict = {"asset": copy.deepcopy(background.get("source") or {})}
            if background.get("fit"):
                image["fit"] = background["fit"]
            return [_op("background.image", at_slide, image, "template")]
        return [_op("background.inherited", at_slide, {})]
    if kind == "delete":
        return [_op("object.delete", at_object, {})]
    if kind == "add_text":
        args: JsonDict = {
            "text": str(override["text"]),
            "bbox": copy.deepcopy(override["geometry"]["bbox"]),
            "new_object_id": str(target["object_id"]),
        }
        if override.get("style"):
            args["style"] = copy.deepcopy(override["style"])
        return [
            _op(
                "object.add_text",
                {"scope": "slide", "slide": slide},
                args,
                _text_source(args["text"]),
            )
        ]
    raise ValueError(f"неизвестная правка плана: {kind}")


def _text_source(text: str) -> str:
    return "package" if "{fact:" in text else "snapshot"


def ops_from_patch(patch: JsonDict) -> list[JsonDict]:
    """Операции запроса `slide_patch`: правки слайдов по порядку, затем порядок и знак."""
    ops: list[JsonDict] = []
    for item in patch.get("slides") or []:
        slide = {"slide_id": str(item["slide_id"])}
        for override in item.get("overrides") or []:
            ops.extend(ops_from_override(override, slide))
    if patch.get("order"):
        ops.append(_op("deck.move", {"scope": "deck"}, {"order": list(patch["order"])}))
    if patch.get("template_logo"):
        ops.append(
            _op("deck.logo", {"scope": "deck"}, {"action": patch["template_logo"]}, "template")
        )
    return ops


class NotPlanExpressibleError(ValueError):
    """Операция не выражается правкой плана: ей нужен свой исполнитель (этапы 38–44)."""


def overrides_from_ops(ops: list[JsonDict], snapshot: JsonDict | None = None) -> list[JsonDict]:
    """Правки плана из операций, выразимых планом. Подряд идущие операции стиля одного объекта
    собираются в одну правку style, перемещение и размер — в geometry, замена и перекраска
    картинки — в picture. Одиночное перемещение или размер берут недостающее из снимка."""
    out: list[JsonDict] = []
    slides: dict[int, JsonDict] = {}
    for op in ops:
        name, args = op["op"], op.get("args") or {}
        address = op.get("address") or {}
        target = _target(address.get("object")) if address.get("object") else None
        last = out[-1] if out else None
        same = last is not None and target is not None and last.get("target") == target
        if name == "text.set" and address.get("scope") == "object":
            out.append({"op": "text", "target": target, "text": args["text"]})
        elif name in STYLE_KEYS or name == "style.align":
            style = last.get("style") if same and last and last["op"] == "style" else None
            key, arg = STYLE_KEYS.get(name, ("align", "align"))
            taken = set((style or {}).get("font") or {}) | ({"align"} & set(style or {}))
            if style is None or key in taken:
                style = {}
                out.append({"op": "style", "target": target, "style": style})
            if arg not in args:
                raise NotPlanExpressibleError(f"{name} ступенью или по имени — нужен исполнитель")
            if name == "style.align":
                style["align"] = args["align"]
            else:
                style.setdefault("font", {})[key] = args[arg]
        elif name in ("object.move", "object.resize"):
            if not {"x", "y", "width", "height"} & set(args) or set(args) - {
                "x",
                "y",
                "width",
                "height",
            }:
                raise NotPlanExpressibleError(f"{name} словами или сдвигом — нужен исполнитель")
            if (
                same
                and last
                and last["op"] == "geometry"
                and not set(args) & set(last["geometry"]["bbox"])
            ):
                last["geometry"]["bbox"].update(args)
            else:
                out.append({"op": "geometry", "target": target, "geometry": {"bbox": dict(args)}})
                slides[id(out[-1])] = address.get("slide") or {}
        elif name == "picture.replace":
            picture = {"source": copy.deepcopy(args["asset"])}
            if args.get("fit"):
                picture["fit"] = args["fit"]
            out.append({"op": "picture", "target": target, "picture": picture})
        elif name == "picture.recolor" and same and last and last["op"] == "picture":
            if "color" not in args:
                raise NotPlanExpressibleError("перекраска цветом по имени — нужен исполнитель")
            last["picture"]["color"] = args["color"]
        elif name == "background.solid" and "color" in args:
            solid = {"kind": "solid", "color": args["color"]}
            out.append({"op": "background", "background": solid})
        elif name == "background.image":
            background = {"kind": "image", "source": copy.deepcopy(args["asset"])}
            if args.get("fit"):
                background["fit"] = args["fit"]
            out.append({"op": "background", "background": background})
        elif name == "background.inherited":
            out.append({"op": "background", "background": {"kind": "inherited"}})
        elif name == "object.delete" and address.get("scope") == "object":
            out.append({"op": "delete", "target": target})
        elif name == "object.add_text" and "bbox" in args and args.get("new_object_id"):
            add: JsonDict = {
                "op": "add_text",
                "target": {"object_id": args["new_object_id"]},
                "text": args["text"],
                "geometry": {"bbox": copy.deepcopy(args["bbox"])},
            }
            if args.get("style"):
                add["style"] = copy.deepcopy(args["style"])
            out.append(add)
        else:
            raise NotPlanExpressibleError(f"{name} не выражается правкой плана")
    for override in out:
        if override["op"] == "geometry":
            _complete_geometry(override, slides.get(id(override)) or {}, snapshot)
    return out


def _target(ref: JsonDict | None) -> JsonDict:
    keys = ("object_id", "slot_id", "source_object_id")
    return {k: v for k, v in (ref or {}).items() if k in keys}


def _complete_geometry(override: JsonDict, slide: JsonDict, snapshot: JsonDict | None) -> None:
    box = override["geometry"]["bbox"]
    missing = {"x", "y", "width", "height"} - set(box)
    if not missing:
        return
    found = _find_slide((snapshot or {}).get("slides") or [], slide) if slide else None
    obj = _find_object(found, override["target"]) if found else None
    if obj is None:
        raise NotPlanExpressibleError("перемещение без размера: объекта нет в снимке")
    for key in missing:
        box[key] = obj["bbox"][key]


__all__ = [
    "NotPlanExpressibleError",
    "check_addresses",
    "ops_from_override",
    "ops_from_patch",
    "overrides_from_ops",
    "validate",
]
