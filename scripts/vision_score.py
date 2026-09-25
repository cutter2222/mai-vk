"""Оценка вёрстки прогона глазами модели: все PNG слайдов из `runs/quality/<метка>/`.

Тот же промпт, что у скилла visual_reviewer в конвейере, но снаружи и по готовым картинкам:
одинаковая мерка для прогонов до и после правок. Итог — дефекты по видам и доля слайдов
с дефектом; подробности в `<метка>/vision_score.json`.

    set -a && . ./.env && set +a
    uv run python scripts/vision_score.py base-1
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import collections
import json
import os
import pathlib
import re
from typing import Any

from openai import AsyncOpenAI

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROMPT_FILE = ROOT / "skills/visual_reviewer/prompts/review.layout.v0.1.0.md"
CODES = ("clipped", "overlap", "tiny", "leftover", "empty")


def prompt() -> str:
    text = PROMPT_FILE.read_text(encoding="utf-8")
    return text.split("---", 2)[2].strip() if text.startswith("---") else text


async def judge(client: AsyncOpenAI, model: str, path: pathlib.Path, gate: Any) -> list[str]:
    data = base64.b64encode(path.read_bytes()).decode()
    async with gate:
        for attempt in range(3):
            try:
                r = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": prompt()},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Слайд для проверки."},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/png;base64,{data}"},
                                },
                            ],
                        },
                    ],
                    response_format={"type": "json_object"},
                    temperature=0,
                    max_tokens=600,
                    extra_body={"reasoning": {"enabled": False}},
                )
                parsed = json.loads(r.choices[0].message.content or "{}")
                return [
                    str(i.get("code"))
                    for i in parsed.get("issues") or []
                    if isinstance(i, dict) and i.get("code") in CODES
                ]
            except Exception:
                if attempt == 2:
                    return ["error"]
                await asyncio.sleep(2)
    return ["error"]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("label")
    parser.add_argument("--model", default="qwen/qwen3.8-27b")
    parser.add_argument("--variants", default="compact,balanced,detailed")
    args = parser.parse_args()
    base = ROOT / "runs" / "quality" / args.label
    pattern = re.compile(rf"^({'|'.join(args.variants.split(','))})-\d+\.png$")
    slides = sorted(p for p in base.rglob("*.png") if pattern.match(p.name))
    client = AsyncOpenAI(
        base_url=os.environ["PD_OPENROUTER_BASE_URL"], api_key=os.environ["PD_OPENROUTER_API_KEY"]
    )
    gate = asyncio.Semaphore(8)
    results = await asyncio.gather(*(judge(client, args.model, p, gate) for p in slides))
    per: dict[str, list[str]] = {
        str(p.relative_to(base)): r for p, r in zip(slides, results, strict=True)
    }
    total: collections.Counter[str] = collections.Counter(c for r in results for c in r)
    bad = sum(1 for r in results if any(c != "error" for c in r))
    (base / "vision_score.json").write_text(
        json.dumps(
            {"by_code": dict(total), "bad_slides": bad, "slides": per}, ensure_ascii=False, indent=1
        )
    )
    print(f"слайдов {len(slides)}, с дефектом {bad} ({bad / max(len(slides), 1):.0%})")
    print("дефекты по видам:", dict(total.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
