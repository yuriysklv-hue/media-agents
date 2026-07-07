#!/usr/bin/env python3
"""Недельный отчёт по расходам LLM: читает data/state/llm_usage.jsonl.

Usage: python scripts/cost_report.py [--days 7]
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.state import StateManager, read_jsonl  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    state = StateManager()
    usage = read_jsonl(state.state_dir / "llm_usage.jsonl")
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    usage = [u for u in usage if u.get("timestamp", "") >= since]
    if not usage:
        print(f"Нет LLM-вызовов за последние {args.days} дн.")
        return 0

    by_model: dict[tuple, dict] = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "cost": 0.0})
    by_stage: dict[str, float] = defaultdict(float)
    for u in usage:
        key = (u.get("provider"), u.get("model"))
        row = by_model[key]
        row["calls"] += 1
        row["in"] += u.get("input_tokens", 0)
        row["out"] += u.get("output_tokens", 0)
        row["cost"] += u.get("cost_usd", 0)
        by_stage[u.get("stage", "?")] += u.get("cost_usd", 0)

    total = sum(r["cost"] for r in by_model.values())
    published = [
        r for r in state.load_published()
        if r.get("published_at", "") >= since
    ]

    print(f"=== LLM-расходы за {args.days} дн. ===\n")
    print(f"{'Провайдер':<12}{'Модель':<22}{'Вызовы':>8}{'In-токены':>12}{'Out-токены':>12}{'$':>10}")
    for (provider, model), row in sorted(by_model.items()):
        print(f"{provider:<12}{model:<22}{row['calls']:>8}{row['in']:>12}{row['out']:>12}{row['cost']:>10.4f}")
    print(f"\nИтого: ${total:.4f}")
    print("\nПо этапам:")
    for stage, cost in sorted(by_stage.items(), key=lambda kv: -kv[1]):
        print(f"  {stage:<16}${cost:.4f}")
    if published:
        print(f"\nОпубликовано материалов: {len(published)}")
        print(f"Стоимость на материал: ${total / len(published):.4f} (таргет ТЗ: $0.30–0.50)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
