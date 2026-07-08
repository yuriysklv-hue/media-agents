"""Filter + Dedup: повторная фильтрация на русском, семантическая дедупликация,
кластеризация похожих items в события.

- Порог 0.85 cosine similarity — дубликат уже опубликованного;
- порог 0.75 — кластеризация новых items в одно событие.
При недоступности embedding API дедупликация пропускается (URL-дедуп уже был).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..llm_client import LLMUnavailable, parse_json_response, pipeline_client
from ..utils.config import DATA_DIR, fill_prompt, load_prompt
from ..utils.logger import get_logger
from ..utils.state import StateManager, read_jsonl, utcnow_iso, write_jsonl

log = get_logger("filter_dedup")

TRANSLATED_PATH = DATA_DIR / "inbox" / "translated_items.jsonl"
CURATED_PATH = DATA_DIR / "inbox" / "curated_items.jsonl"

RELEVANCE_THRESHOLD = 5
DUP_SIMILARITY = 0.85
CLUSTER_SIMILARITY = 0.75


@dataclass
class DedupResult:
    total: int = 0
    relevant: int = 0
    duplicates: int = 0
    events: int = 0
    embeddings_skipped: bool = False
    errors: list[str] = field(default_factory=list)


def _refilter_ru(items: list[dict], state: StateManager) -> list[dict]:
    """Повторная оценка релевантности уже на русском (GLM). Мягкая: при сбое — пропуск."""
    try:
        client, model = pipeline_client("filter_dedup_classify", state)
    except LLMUnavailable:
        log.warning("GLM недоступен — повторная фильтрация на русском пропущена")
        return items

    template = load_prompt("pre_filter")
    payload = [
        {"id": it["id"], "title": it["title_ru"], "summary": it["summary_ru"][:500]}
        for it in items
    ]
    try:
        answer = client.chat(
            model=model, system="",
            user=fill_prompt(template, items_json=json.dumps(payload, ensure_ascii=False)),
            temperature=0.0, max_tokens=4096, stage="filter_dedup",
        )
        scores = {str(r["id"]): int(r.get("score", 0)) for r in parse_json_response(answer)}
    except Exception as exc:
        log.warning("повторная фильтрация не отработала: %s — оставляем все items", exc)
        return items

    kept = []
    for item in items:
        score = scores.get(item["id"])
        if score is not None and score < RELEVANCE_THRESHOLD:
            log.info("отброшено после перевода (%d): %s", score, item["title_ru"][:70])
            continue
        item["relevance_score_ru"] = score
        kept.append(item)
    return kept


def _embed_items(items: list[dict], state: StateManager):
    """Векторы items (по title_ru + summary_ru). None — embedding недоступен."""
    try:
        client, model = pipeline_client("filter_dedup_embedding", state)
    except LLMUnavailable:
        return None
    from openai import BadRequestError, NotFoundError
    import numpy as np

    vectors = []
    for item in items:
        text = f"{item['title_ru']}\n{item['summary_ru']}"
        try:
            vectors.append(client.embed(model, text, stage="filter_dedup", item_id=item["id"]))
        except (BadRequestError, NotFoundError) as exc:
            # Платформа без эмбеддингов (z.ai) — дальше пробовать смысла нет.
            log.warning("модель эмбеддингов «%s» недоступна (%s) — семантический "
                        "дедуп отключён, остаётся URL-дедуп", model, str(exc)[:100])
            return None
        except Exception as exc:
            log.warning("embedding для %s не получен: %s", item["id"], exc)
            vectors.append(None)
    if all(v is None for v in vectors):
        return None
    dim = len(next(v for v in vectors if v is not None))
    matrix = np.array(
        [v if v is not None else [0.0] * dim for v in vectors], dtype=np.float32
    )
    return matrix


def _cosine_matrix(a, b):
    import numpy as np

    a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)
    b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-9)
    return a_norm @ b_norm.T


def _cluster_topic(cluster: list[dict], state: StateManager) -> str:
    """Однострочное описание события (GLM). Фолбэк — заголовок primary-источника."""
    fallback = cluster[0]["title_ru"]
    if len(cluster) == 1:
        return fallback
    try:
        client, model = pipeline_client("filter_dedup_classify", state)
        titles = "\n".join(f"- {it['title_ru']}" for it in cluster)
        answer = client.chat(
            model=model, system="",
            user=("Ниже заголовки новостей об одном событии. Опиши событие одним "
                  f"предложением на русском, без вступлений:\n{titles}"),
            temperature=0.2, max_tokens=200, stage="filter_dedup",
        )
        return answer.strip().strip('"') or fallback
    except Exception:
        return fallback


def _make_event(cluster: list[dict], state: StateManager) -> dict:
    # Primary — максимальный скор релевантности, при равенстве — самый полный текст.
    def rank(it: dict):
        return (it.get("llm_score") or it.get("relevance_score_ru") or 0,
                len(it.get("content_ru", "")))

    ordered = sorted(cluster, key=rank, reverse=True)
    primary = ordered[0]
    sources = []
    for it in ordered:
        sources.append({
            "source_name": it["source_name"],
            "source_url": it["source_url"],
            "title_original": it["title"],
            "title_ru": it["title_ru"],
            "summary_ru": it["summary_ru"],
            "content_ru": it.get("content_ru", ""),
            "individual_score": rank(it)[0],
            "is_primary": it is primary,
        })
    return {
        "event_id": primary["id"],
        "created_at": utcnow_iso(),
        "cluster_topic": _cluster_topic(ordered, state),
        "relevance_score": rank(primary)[0],
        "published_at": primary.get("published_at"),
        "region": primary.get("region", "world"),
        "sources": sources,
        "source_count": len(sources),
        "is_duplicate": False,
        "duplicate_of": None,
    }


def run_filter_dedup(state: StateManager) -> DedupResult:
    """translated_items.jsonl → curated_items.jsonl (уникальные события)."""
    import numpy as np

    result = DedupResult()
    items = read_jsonl(TRANSLATED_PATH)
    result.total = len(items)
    if not items:
        write_jsonl(CURATED_PATH, [])
        return result

    items = _refilter_ru(items, state)
    result.relevant = len(items)
    if not items:
        write_jsonl(CURATED_PATH, [])
        return result

    matrix = _embed_items(items, state)
    if matrix is None:
        log.warning("embeddings недоступны — семантическая дедупликация пропущена")
        result.embeddings_skipped = True
        events = [_make_event([it], state) for it in items]
        write_jsonl(CURATED_PATH, events)
        result.events = len(events)
        return result

    # Дубликаты против опубликованного.
    published_vectors, _slugs = state.load_published_embeddings()
    fresh_idx = list(range(len(items)))
    if published_vectors is not None and len(published_vectors) > 0:
        sim = _cosine_matrix(matrix, np.asarray(published_vectors, dtype=np.float32))
        fresh_idx = [i for i in fresh_idx if float(sim[i].max()) <= DUP_SIMILARITY]
        result.duplicates = result.relevant - len(fresh_idx)
        if result.duplicates:
            log.info("отброшено дубликатов опубликованного: %d", result.duplicates)

    # Кластеризация новых (жадная, по порогу 0.75).
    clusters: list[list[int]] = []
    assigned: set[int] = set()
    pair_sim = _cosine_matrix(matrix, matrix)
    for i in fresh_idx:
        if i in assigned:
            continue
        cluster = [i]
        assigned.add(i)
        for j in fresh_idx:
            if j in assigned or j == i:
                continue
            if float(pair_sim[i][j]) > CLUSTER_SIMILARITY:
                cluster.append(j)
                assigned.add(j)
        clusters.append(cluster)

    events = []
    for cluster in clusters:
        event = _make_event([items[i] for i in cluster], state)
        event["embedding"] = [float(x) for x in matrix[cluster[0]]]
        events.append(event)

    write_jsonl(CURATED_PATH, events)
    result.events = len(events)
    log.info("итог: %d событий из %d items", result.events, result.total)
    return result
