"""Проверка примеров из contracts/examples по схемам из contracts/schemas.

Имя примера: `<schema>.example.json` или `<schema>.<variant>.example.json`.
Запуск: uv run python contracts/validate.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent
SCHEMAS = ROOT / "schemas"
EXAMPLES = ROOT / "examples"

try:
    import jsonschema
    from referencing import Registry, Resource
except ImportError:
    print("Установите зависимости: uv sync (нужны jsonschema и referencing)")
    sys.exit(2)


def load_registry() -> tuple[Registry, dict[str, dict]]:
    registry = Registry()
    schemas: dict[str, dict] = {}
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        schema = json.loads(path.read_text())
        schemas[path.name] = schema
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return registry, schemas


def schema_name_for(example: pathlib.Path) -> str:
    base = example.name[: -len(".example.json")]
    return base.split(".")[0] + ".schema.json"


def main() -> int:
    registry, schemas = load_registry()
    failed = 0
    checked = 0
    for schema in schemas.values():
        jsonschema.Draft202012Validator.check_schema(schema)
    for example in sorted(EXAMPLES.glob("*.example.json")):
        schema_name = schema_name_for(example)
        if schema_name not in schemas:
            failed += 1
            print(f"FAIL {example.name}: нет схемы {schema_name}")
            continue
        validator = jsonschema.Draft202012Validator(schemas[schema_name], registry=registry)
        doc = json.loads(example.read_text())
        errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
        checked += 1
        if errors:
            failed += 1
            print(f"FAIL {example.name}")
            for err in errors[:20]:
                where = "/".join(str(p) for p in err.path) or "<root>"
                print(f"    {where} -> {err.message[:200]}")
        else:
            print(f"ok   {example.name}")
    schema_without_example = [
        n
        for n in schemas
        if n != "common.schema.json"
        and not any(schema_name_for(e) == n for e in EXAMPLES.glob("*.example.json"))
    ]
    for n in schema_without_example:
        failed += 1
        print(f"FAIL схема без примера: {n}")
    print(f"{checked} примеров, {len(schemas)} схем, ошибок: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
