"""Оркестрация пайплайна Базы знаний (вызывается из spravochnik.yml / вручную).

Порядок за один прогон:
  0. Sync merged PRs — status review→published, если PR смёржен на сайт.
  1. Queue Manager   — следующий pending|revision (иначе выход).
  2. Researcher      — Wikipedia + related из живой коллекции media.
  3. Writer          — материал по типу + сноска РКН + finalize.
  4. Fact-checker    — сверка с Wikipedia (best-effort); critical → needs_fix.
  4b. Rules-QA       — схема/длины/разделы/кодировка; FAIL → needs_fix.
  5. Publisher       — draft-PR; очередь → review.

Usage:
  python -m src.spravochnik.run              # один материал
  python -m src.spravochnik.run --dry-run    # без PR (черновик в data/)
  python -m src.spravochnik.run --limit 3    # до 3 материалов за прогон
"""
from __future__ import annotations

import argparse
import os
import sys

from ..utils.config import ensure_data_dirs, env_flag
from ..utils.logger import get_logger
from ..utils.store import ArticleStore
from . import queue_manager as qm

log = get_logger("spravochnik.run")


def sync_merged_prs(store: ArticleStore) -> int:
    """review-айтемы с смёрженным PR → published. Возвращает число обновлённых."""
    from .publisher import is_pr_merged

    if env_flag("DRY_RUN"):
        return 0
    updated = 0
    for item in qm.load_queue():
        if item.get("status") == "review" and item.get("pr_number"):
            try:
                if is_pr_merged(item["pr_number"]):
                    from ..utils.state import utcnow_iso
                    qm.update_status(item["id"], "published", published_at=utcnow_iso())
                    updated += 1
            except Exception as exc:  # noqa: BLE001 — sync не должен ронять прогон
                log.warning("sync PR #%s (%s): %s", item.get("pr_number"), item["id"], exc)
    if updated:
        log.info("sync: %d айтем(ов) → published", updated)
    return updated


def process_one(item: dict, store: ArticleStore) -> str:
    """Полный цикл по одному айтему. Возвращает итоговый статус для лога."""
    from . import qa
    from .fact_checker import check_facts
    from .publisher import publish
    from .researcher import gather_research
    from .writer import write_material

    item_id = item["id"]
    qm.update_status(item_id, "writing")
    try:
        research = gather_research(item)
        draft = write_material(item, research, store)
    except Exception as exc:  # noqa: BLE001 — генерация/парсинг упали
        log.error("айтем %s: генерация не удалась: %s", item_id, exc)
        qm.update_status(item_id, "needs_fix", feedback=f"generation error: {exc}")
        return "needs_fix"

    fact = check_facts(draft, research, store)
    if not fact.passed:
        qm.update_status(item_id, "needs_fix", feedback="; ".join(fact.critical_errors))
        log.warning("айтем %s: фактчек critical → needs_fix", item_id)
        return "needs_fix"

    qa_result = qa.run_qa(draft, store)
    if qa_result.status != "PASS":
        qm.update_status(item_id, "needs_fix", feedback="; ".join(qa_result.errors))
        log.warning("айтем %s: rules-QA FAIL → needs_fix", item_id)
        return "needs_fix"

    result = publish(item, draft)
    if result.dry_run:
        qm.update_status(item_id, "review")  # статус двигаем, PR нет (dry-run)
        return "review (dry-run)"
    qm.update_status(item_id, "review", pr_url=result.pr_url, pr_number=result.pr_number)
    log.info("айтем %s: PR %s", item_id, result.pr_url)
    return "review"


def main() -> int:
    parser = argparse.ArgumentParser(description="Пайплайн Базы знаний")
    parser.add_argument("--dry-run", action="store_true", help="без PR в media")
    parser.add_argument("--limit", type=int, default=1, help="материалов за прогон")
    args = parser.parse_args()
    if args.dry_run:
        os.environ["DRY_RUN"] = "true"

    ensure_data_dirs()
    store = ArticleStore()

    sync_merged_prs(store)

    processed = 0
    for _ in range(max(1, args.limit)):
        item = qm.get_next_item()
        if item is None:
            log.info("очередь пуста (нет pending|revision) — выход")
            break
        log.info("=== обработка %s (%s, iteration %s) ===",
                 item["id"], item["type"], item.get("iteration", 0))
        status = process_one(item, store)
        log.info("=== %s → %s ===", item["id"], status)
        processed += 1

    log.info("прогон завершён: обработано %d материал(ов)", processed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
