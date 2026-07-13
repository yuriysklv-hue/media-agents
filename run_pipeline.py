#!/usr/bin/env python3
"""Главный скрипт пайплайна новостей.

Usage:
  python run_pipeline.py                    # Полный цикл
  python run_pipeline.py --collect-only     # Только сбор
  python run_pipeline.py --skip-collect     # Обработка существующих raw_items
  python run_pipeline.py --dry-run          # Без git-операций
  python run_pipeline.py --stage translator # Запуск только одного этапа
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from src.utils.config import DATA_DIR, ensure_data_dirs, load_config
from src.utils.logger import get_logger
from src.utils.state import StateManager, read_jsonl

log = get_logger("pipeline")

STAGES = ("collect", "pre_filter", "translator", "filter_dedup", "write", "publish")


def stage_collect(state: StateManager) -> dict:
    from src.collectors.rss_collector import collect_rss

    result = collect_rss(load_config("sources"), state)
    state.set_last_run("collect")
    return {"raw_items": result.added, "collect_errors": len(result.errors)}


def stage_pre_filter(state: StateManager) -> dict:
    from src.processors.pre_filter import run_pre_filter

    result = run_pre_filter(state)
    state.set_last_run("pre_filter")
    return {"passed_pre_filter": result.passed}


def stage_translator(state: StateManager) -> dict:
    from src.processors.translator import run_translator

    result = run_translator(state)
    state.set_last_run("translator")
    return {"translated": result.translated + result.from_cache}


def stage_filter_dedup(state: StateManager) -> dict:
    from src.processors.filter_dedup import run_filter_dedup

    result = run_filter_dedup(state)
    state.set_last_run("filter_dedup")
    return {"curated": result.events, "duplicates": result.duplicates}


def stage_write(state: StateManager) -> dict:
    """Author News → Enricher → QA для каждого curated_item.

    При отказе QA по стилю/тону (главная причина брака — «ИИ-голос», задача 4)
    делаем один проход переписывания: скармливаем писателю конкретные замечания
    QA и прогоняем write→enrich→qa заново. В failed уходит только то, что не
    прошло и после переписывания.
    """
    from src.processors.enricher import enrich_draft
    from src.processors.qa import run_qa
    from src.writers.news_writer import write_news

    def _write_enrich_qa(event: dict, feedback: str | None = None):
        draft = write_news(event, state, feedback=feedback)
        draft = enrich_draft(draft, state, article_type="news",
                             region=event.get("region", "world"))
        primary = next((s for s in event["sources"] if s.get("is_primary")), {})
        qa = run_qa(draft, state,
                    source_content=primary.get("content_ru", ""), article_type="news")
        return draft, qa

    events = read_jsonl(DATA_DIR / "inbox" / "curated_items.jsonl")
    passed, failed, recovered = [], 0, 0
    for event in events:
        try:
            draft, qa = _write_enrich_qa(event)
            if qa.status == "FAIL" and qa.retryable_style:
                log.info("событие %s: QA завернул по стилю — переписываю по замечаниям: %s",
                         event.get("event_id"), "; ".join(qa.llm_issues) or "без деталей")
                draft, qa = _write_enrich_qa(event, feedback="; ".join(qa.llm_issues))
                if qa.status == "PASS":
                    recovered += 1
            if qa.status == "PASS":
                passed.append((draft, event))
            else:
                failed += 1
        except Exception as exc:
            log.error("событие %s не обработано: %s", event.get("event_id"), exc)
            failed += 1
    state.set_last_run("write")
    # Список PASS-файлов для publish — сохраняем в памяти процесса через metrics.
    stage_write.passed = passed  # type: ignore[attr-defined]
    return {"qa_passed": len(passed), "qa_failed": failed, "qa_recovered": recovered}


def stage_publish(state: StateManager, metrics: dict) -> dict:
    from src.publishers.git_publisher import PublishItem, publish

    passed = getattr(stage_write, "passed", None)
    if passed is None:
        # Этап write не выполнялся в этом процессе — публикуем всё из drafts/news.
        drafts = sorted((DATA_DIR / "drafts" / "news").glob("*.md"))
        passed = [(p, None) for p in drafts if not p.name.startswith("draft-")]
    items = [PublishItem(path=p, article_type="news", event=e) for p, e in passed]
    result = publish(items, state, metrics=metrics)
    state.set_last_run("publish")
    return {"published": result.published, "pr_url": result.pr_url}


def total_cost_since(state: StateManager, started_iso: str) -> float:
    usage = read_jsonl(state.state_dir / "llm_usage.jsonl")
    return round(sum(u.get("cost_usd", 0) for u in usage if u.get("timestamp", "") >= started_iso), 4)


def main() -> int:
    parser = argparse.ArgumentParser(description="Пайплайн новостей media-agents")
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--skip-collect", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stage", choices=STAGES, help="запустить только один этап")
    args = parser.parse_args()

    if args.dry_run:
        os.environ["DRY_RUN"] = "true"

    ensure_data_dirs()
    state = StateManager()
    started = time.monotonic()
    from src.utils.state import utcnow_iso

    started_iso = utcnow_iso()
    metrics: dict = {}

    if args.stage:
        plan = [args.stage]
    elif args.collect_only:
        plan = ["collect"]
    else:
        plan = [s for s in STAGES if not (args.skip_collect and s == "collect")]

    log.info("=== запуск пайплайна: %s ===", " → ".join(plan))
    runners = {
        "collect": stage_collect,
        "pre_filter": stage_pre_filter,
        "translator": stage_translator,
        "filter_dedup": stage_filter_dedup,
        "write": stage_write,
    }
    try:
        for stage in plan:
            if stage == "publish":
                metrics["llm_cost_usd"] = total_cost_since(state, started_iso)
                metrics["duration"] = f"{time.monotonic() - started:.0f}s"
                metrics.update(stage_publish(state, metrics))
            else:
                metrics.update(runners[stage](state))
            log.info("этап %s завершён: %s", stage, metrics)
    except Exception as exc:
        log.error("пайплайн остановлен: %s", exc)
        return 1

    metrics.setdefault("llm_cost_usd", total_cost_since(state, started_iso))
    log.info("=== пайплайн завершён за %.0f сек: %s ===", time.monotonic() - started, metrics)
    return 0


if __name__ == "__main__":
    sys.exit(main())
