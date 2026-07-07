#!/usr/bin/env python3
"""Дайджест недели.

Usage:
  python run_digest.py                  # Текущая неделя
  python run_digest.py --week 2026-W27  # Конкретная неделя
  python run_digest.py --dry-run        # Без PR
"""
from __future__ import annotations

import argparse
import os
import sys

from src.utils.config import ensure_data_dirs
from src.utils.logger import get_logger
from src.utils.state import StateManager

log = get_logger("digest")


def main() -> int:
    parser = argparse.ArgumentParser(description="Недельный дайджест media-agents")
    parser.add_argument("--week", help="ISO-неделя вида 2026-W27 (по умолчанию — текущая)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        os.environ["DRY_RUN"] = "true"

    ensure_data_dirs()
    state = StateManager()

    from src.processors.enricher import enrich_draft
    from src.processors.qa import run_qa
    from src.publishers.git_publisher import PublishItem, publish
    from src.writers.digest_writer import iso_week, write_digest

    week = args.week or iso_week()
    try:
        draft = write_digest(state, week)
        if draft is None:
            return 0
        draft = enrich_draft(draft, state, article_type="digest")
        qa = run_qa(draft, state, article_type="digest")
        if qa.status != "PASS":
            log.error("дайджест %s не прошёл QA: %s", week, "; ".join(qa.errors))
            return 1
        result = publish(
            [PublishItem(path=draft, article_type="digest")],
            state,
            metrics={"raw_items": "—", "passed_pre_filter": "—", "translated": "—",
                     "curated": "—", "qa_failed": 0},
            branch=f"digest-{week.lower()}",
            pr_title=f"Дайджест недели {week}",
        )
        log.info("дайджест %s: PR %s", week, result.pr_url or "(dry-run)")
    except Exception as exc:
        log.error("дайджест не собран: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
