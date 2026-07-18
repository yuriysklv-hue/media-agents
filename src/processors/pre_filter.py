"""Pre-Filter: отбраковка нерелевантного ДО перевода. Двухуровневый.

Уровень 1 — keyword scoring (детерминированный, без LLM).
Уровень 2 — GLM-4-Flash, семантическая оценка 0-10 пачками по 50 items.
При недоступности GLM уровень 2 пропускается (алерт в лог), пайплайн работает.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from ..llm_client import LLMUnavailable, parse_json_response, pipeline_client
from ..utils.config import DATA_DIR, env_flag, fill_prompt, load_config, load_prompt
from ..utils.logger import get_logger
from ..utils.scoring import passes_keyword_filter
from ..utils.state import StateManager, read_jsonl, utcnow_iso, write_jsonl

log = get_logger("pre_filter")

RAW_PATH = DATA_DIR / "inbox" / "raw_items.jsonl"
PASSED_PATH = DATA_DIR / "inbox" / "passed_pre_filter.jsonl"

BATCH_SIZE = 50
LLM_SCORE_THRESHOLD = 5


def select_keywords(item: dict, dicts: dict) -> dict:
    """Словарь Pre-Filter для item: по РЕГИОНУ (asia), затем по языку (ru), иначе en.

    asia приходит на английском (как мир) → по языку не отличить, поэтому регион
    проверяем первым. ru приходит на русском → своя ветка по языку.
    `dicts` = {"en", "ru", "asia"} → загруженные keyword-конфиги.
    """
    if item.get("region") == "asia":
        return dicts["asia"]
    return dicts.get(item.get("language", "en"), dicts["en"])


def _relevance(item: dict) -> tuple[float, float]:
    """Ключ ранжирования: LLM-скор приоритетнее, иначе keyword-скор."""
    llm = item.get("llm_score")
    return (float(llm) if llm is not None else -1.0, float(item.get("keyword_score", 0)))


@dataclass
class PreFilterResult:
    total: int = 0
    passed_keywords: int = 0
    passed: int = 0
    llm_skipped: bool = False
    errors: list[str] = field(default_factory=list)


def _llm_scores(items: list[dict], state: StateManager) -> dict[str, dict]:
    """id → {score, reason} от GLM. Пустой dict, если LLM недоступен."""
    try:
        client, model = pipeline_client("pre_filter", state)
    except LLMUnavailable as exc:
        log.warning("GLM недоступен (%s) — pre-filter только по ключевым словам", exc)
        return {}

    template = load_prompt("pre_filter")
    scores: dict[str, dict] = {}
    for start in range(0, len(items), BATCH_SIZE):
        batch = items[start:start + BATCH_SIZE]
        payload = [
            {"id": it["id"], "title": it["title"], "summary": it["summary"][:500]}
            for it in batch
        ]
        prompt = fill_prompt(template, items_json=json.dumps(payload, ensure_ascii=False))
        try:
            answer = client.chat(
                model=model, system="", user=prompt,
                temperature=0.0, max_tokens=4096,
                response_format={"type": "json_object"} if len(batch) == 1 else None,
                stage="pre_filter",
            )
            for row in parse_json_response(answer):
                scores[str(row["id"])] = {
                    "score": int(row.get("score", 0)),
                    "reason": str(row.get("reason", "")),
                }
        except Exception as exc:
            log.warning("GLM-батч %d..%d не отработал: %s — батч идёт без LLM-скора",
                        start, start + len(batch), exc)
    return scores


def run_pre_filter(state: StateManager, since: str | None = None) -> PreFilterResult:
    """raw_items.jsonl → passed_pre_filter.jsonl.

    since: ISO-timestamp — обрабатывать только items, собранные после него
    (по умолчанию — отметка последнего прогона pre_filter).
    """
    result = PreFilterResult()
    raw_items = read_jsonl(RAW_PATH)
    since = since or state.get_last_run("pre_filter")
    if since:
        raw_items = [it for it in raw_items if it.get("collected_at", "") > since]
    result.total = len(raw_items)
    if not raw_items:
        write_jsonl(PASSED_PATH, [])
        log.info("нет новых raw_items — pre-filter пропущен")
        return result

    keywords_en = load_config("keywords")
    dicts = {"en": keywords_en, "ru": load_config("keywords_ru"),
             "asia": load_config("keywords_asia")}

    survivors = []
    for item in raw_items:
        keywords_config = select_keywords(item, dicts)
        score = passes_keyword_filter(item["title"], item["summary"], keywords_config)
        if score.score <= 0:
            continue
        item = dict(item)
        item["keyword_score"] = score.score
        item["keyword_matched"] = score.matched
        survivors.append(item)
    result.passed_keywords = len(survivors)
    log.info("keyword scoring: %d из %d прошли порог", len(survivors), result.total)

    # Тест-режим: жёсткий кап на объём, уходящий в дорогие этапы (GLM-скоринг →
    # перевод → написание). Память дедупа в тесте выключена, поэтому без капа
    # объём мог бы раздуться. Кандидатов до GLM берём с запасом (×3), финальный
    # топ-N режем после GLM-оценки (см. ниже). Боевой прогон это не касается.
    test_mode = env_flag("PIPELINE_TEST_MODE")
    test_cap = int(os.environ.get("TEST_MAX_ITEMS", "15"))
    if test_mode and len(survivors) > test_cap * 3:
        survivors.sort(key=lambda it: it.get("keyword_score", 0), reverse=True)
        survivors = survivors[: test_cap * 3]
        log.warning("ТЕСТ-РЕЖИМ: кандидатов урезано до %d (топ по keyword-скору) перед GLM",
                    len(survivors))

    llm_scores = _llm_scores(survivors, state)
    result.llm_skipped = not llm_scores and bool(survivors)

    passed = []
    for item in survivors:
        llm = llm_scores.get(item["id"])
        if llm is None:
            # GLM недоступен или пропустил item — доверяем keyword-скору.
            item["llm_score"], item["llm_reason"] = None, "llm_skipped"
        else:
            item["llm_score"], item["llm_reason"] = llm["score"], llm["reason"]
            if llm["score"] < LLM_SCORE_THRESHOLD:
                continue
        item["pre_filter_passed"] = True
        item["pre_filter_at"] = utcnow_iso()
        passed.append(item)

    # Тест-режим: финальный твёрдый потолок на то, что уйдёт в перевод и написание —
    # берём топ-N самых релевантных. Это гарантия по стоимости и по 45-мин лимиту
    # независимо от того, сколько собралось.
    if test_mode and len(passed) > test_cap:
        passed.sort(key=_relevance, reverse=True)
        passed = passed[:test_cap]
        log.warning("ТЕСТ-РЕЖИМ: в перевод/написание уходит топ-%d самых релевантных", test_cap)

    write_jsonl(PASSED_PATH, passed)
    result.passed = len(passed)
    log.info("pre-filter: %d из %d прошли", result.passed, result.total)
    return result
