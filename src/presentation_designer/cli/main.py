"""Точка входа CLI. Подкоманды подключаются вместе со своими слоями (этапы 2–10)."""

from __future__ import annotations

import argparse
import sys

from presentation_designer import __version__
from presentation_designer.contracts import CONTRACTS_VERSION

PLANNED = ("analyze", "import", "story", "plan", "compose", "export", "audit", "generate")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="presentation-designer", description="Цифровой дизайнер презентаций"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__} (contracts {CONTRACTS_VERSION})",
    )
    sub = parser.add_subparsers(dest="command")
    for name in PLANNED:
        sub.add_parser(name, help="подключается на этапе реализации соответствующего слоя")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    print(
        f"Команда {args.command!r} ещё не реализована: см. IMPLEMENTATION_PLAN.md", file=sys.stderr
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
