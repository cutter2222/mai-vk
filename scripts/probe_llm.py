#!/usr/bin/env python3
"""Тонкая обёртка над зондом провайдера.

Запуск: uv run scripts/probe_llm.py probe --report docs/llm-capabilities.md

Сам зонд — модуль `presentation_designer.llm.probe`, чтобы его можно было запустить и из
контейнера воркера (scripts/ в образы не входит). Ключ и адрес берутся из окружения (.env).
"""

from __future__ import annotations

import sys

from presentation_designer.llm.probe import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
