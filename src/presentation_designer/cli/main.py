"""Точка входа CLI. Подкоманды подключаются вместе со своими слоями (этапы 2–10)."""

from __future__ import annotations

import argparse
import sys

from presentation_designer import __version__
from presentation_designer.cli import analyze as analyze_cmd
from presentation_designer.cli import compose as compose_cmd
from presentation_designer.cli import edit as edit_cmd
from presentation_designer.cli import import_content as import_cmd
from presentation_designer.cli import plan as plan_cmd
from presentation_designer.cli import story as story_cmd
from presentation_designer.contracts import CONTRACTS_VERSION

PLANNED = ("export", "audit", "generate")


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
    analyze_cmd.build_parser(sub.add_parser("analyze", help="профиль шаблона из PPTX"))
    import_cmd.build_parser(sub.add_parser("import", help="контент-пакет из материалов и брифа"))
    story_cmd.build_parser(sub.add_parser("story", help="смысловой план из контент-пакета"))
    plan_cmd.build_parser(sub.add_parser("plan", help="планы трёх вариантов из смыслового плана"))
    compose_cmd.build_parser(
        sub.add_parser("compose", help="PPTX и ComposedDeck по плану варианта")
    )
    edit_cmd.build_parser(
        sub.add_parser("edit-slide", help="правка одного слайда плана по инструкции")
    )
    for name in PLANNED:
        sub.add_parser(name, help="подключается на этапе реализации соответствующего слоя")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "analyze":
        return analyze_cmd.run(args)
    if args.command == "import":
        return import_cmd.run(args)
    if args.command == "story":
        return story_cmd.run(args)
    if args.command == "plan":
        return plan_cmd.run(args)
    if args.command == "compose":
        return compose_cmd.run(args)
    if args.command == "edit-slide":
        return edit_cmd.run(args)
    print(
        f"Команда {args.command!r} ещё не реализована: см. IMPLEMENTATION_PLAN.md", file=sys.stderr
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
