"""Генерирует Pydantic-модели из contracts/schemas одним вызовом datamodel-codegen.

Все схемы собираются в один документ с $defs, чтобы ссылки между файлами
разрешались локально и общие определения не дублировались.
Запуск: uv run python scripts/gen_contracts.py
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "contracts" / "schemas"
OUT = ROOT / "src" / "presentation_designer" / "contracts" / "models.py"
HEADER = "# Сгенерировано scripts/gen_contracts.py из contracts/schemas. Не редактировать вручную."


def rewrite_refs(node: object, self_name: str) -> object:
    """Переписывает ссылки `x.schema.json#/$defs/y` и `#/$defs/y` на объединённый документ."""
    if isinstance(node, dict):
        out: dict[str, object] = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                if value.startswith("#/$defs/"):
                    value = f"#/$defs/{self_name}/$defs/{value[len('#/$defs/') :]}"
                elif value.startswith("#/"):
                    value = f"#/$defs/{self_name}/{value[2:]}"
                elif ".schema.json" in value:
                    file, _, frag = value.partition("#")
                    other = file.replace(".schema.json", "")
                    if frag.startswith("/$defs/"):
                        value = f"#/$defs/{other}/$defs/{frag[len('/$defs/') :]}"
                    elif frag:
                        value = f"#/$defs/{other}/{frag.lstrip('/')}"
                    else:
                        value = f"#/$defs/{other}"
                out[key] = value
            else:
                out[key] = rewrite_refs(value, self_name)
        return out
    if isinstance(node, list):
        return [rewrite_refs(v, self_name) for v in node]
    return node


def main() -> int:
    combined: dict[str, object] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Contracts",
        "type": "object",
        "$defs": {},
        "properties": {},
    }
    defs: dict[str, object] = combined["$defs"]  # type: ignore[assignment]
    props: dict[str, object] = combined["properties"]  # type: ignore[assignment]
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        name = path.name.replace(".schema.json", "")
        schema = json.loads(path.read_text())
        schema.pop("$schema", None)
        schema.pop("$id", None)
        if "title" not in schema or not schema["title"] or name == "common":
            schema["title"] = "Common" if name == "common" else name
        defs[name] = rewrite_refs(schema, name)
        if name != "common":
            props[name] = {"$ref": f"#/$defs/{name}"}

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(combined, tmp, ensure_ascii=False)
        tmp_path = tmp.name

    cmd = [
        "uv",
        "run",
        "datamodel-codegen",
        "--input",
        tmp_path,
        "--input-file-type",
        "jsonschema",
        "--output",
        str(OUT),
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--target-python-version",
        "3.12",
        "--use-schema-description",
        "--use-field-description",
        "--use-title-as-name",
        "--field-constraints",
        "--use-standard-collections",
        "--use-union-operator",
        "--collapse-root-models",
        "--disable-timestamp",
        "--enum-field-as-literal",
        "all",
        "--custom-file-header",
        HEADER,
        "--formatters",
        "ruff-format",
        "--class-name",
        "Contracts",
    ]
    result = subprocess.run(cmd, cwd=ROOT)
    pathlib.Path(tmp_path).unlink(missing_ok=True)
    if result.returncode != 0:
        return result.returncode
    print(f"gen-contracts: {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
