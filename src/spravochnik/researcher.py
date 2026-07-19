"""Researcher: собирает research_bundle для Writer'а.

Два источника: (1) Wikipedia REST — структурированные факты (с нормализацией
тайтла и таймаутом); (2) живая коллекция новостей сайта media — связанные статьи.
Оба источника best-effort: недоступность любого не роняет пайплайн (Writer
получит меньше контекста, фактчек — меньше опоры).
"""
from __future__ import annotations

import re

import requests

from ..utils.logger import get_logger

log = get_logger("spravochnik.researcher")

WIKI_TIMEOUT = 20  # урок run #13: requests без таймаута подвешивает весь прогон
WIKI_UA = "1screen-spravochnik/1.0 (https://1screen.ru)"
RELATED_LIMIT = 5


# --- Wikipedia -------------------------------------------------------------

def _resolve_title(term: str, lang: str) -> str | None:
    """Нормализует произвольный запрос в канонический тайтл статьи.

    page/summary ждёт точный тайтл: «Advantage+»/«MFA-сайты» напрямую не
    резолвятся. REST search/title возвращает ближайшую статью.
    """
    url = f"https://{lang}.wikipedia.org/w/rest.php/v1/search/title"
    try:
        resp = requests.get(url, params={"q": term, "limit": 1},
                            headers={"User-Agent": WIKI_UA}, timeout=WIKI_TIMEOUT)
        if resp.status_code == 200:
            pages = resp.json().get("pages") or []
            if pages:
                return pages[0].get("title")
    except requests.RequestException as exc:
        log.warning("wiki search «%s» (%s): %s", term, lang, exc)
    return None


def _fetch_summary(title: str, lang: str) -> dict | None:
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(title, safe='')}"
    try:
        resp = requests.get(url, headers={"User-Agent": WIKI_UA}, timeout=WIKI_TIMEOUT)
    except requests.RequestException as exc:
        log.warning("wiki summary «%s» (%s): %s", title, lang, exc)
        return None
    if resp.status_code != 200:
        return None
    data = resp.json()
    if data.get("type") == "not_found" or not data.get("extract"):
        return None
    return {
        "source_lang": lang,
        "title": data.get("title"),
        "extract": data.get("extract"),
        "thumbnail": (data.get("thumbnail") or {}).get("source"),
    }


def get_wikipedia_summary(term: str) -> dict | None:
    """Структурированные факты из Wikipedia (ru → en фолбэк).

    None, если статья не найдена ни на одном языке — типично для adtech-продуктов
    и новых терминов (Advantage+, MFA, AI slop). Фактчек это учитывает и не
    блокирует такие материалы.
    """
    for lang in ("ru", "en"):
        title = _resolve_title(term, lang) or term
        data = _fetch_summary(title, lang)
        if data:
            log.info("wiki: «%s» → «%s» (%s)", term, data["title"], lang)
            return data
    log.info("wiki: «%s» не найден — фактчек без опорного источника", term)
    return None


# --- Связанные статьи (живая коллекция сайта) ------------------------------

_WORD_RE = re.compile(r"[\wа-яё]+", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text) if len(t) > 2}


def _relevance(query_tokens: set[str], title: str, body: str) -> float:
    """Лёгкий скор релевантности: пересечение токенов запроса с title (вес ×3)
    и телом. Достаточно для подбора 3–5 связанных новостей; при росте корпуса
    заменяется на TF-IDF-адаптер над tfidf_engine без смены интерфейса.
    """
    if not query_tokens:
        return 0.0
    title_hits = len(query_tokens & _tokens(title)) * 3
    body_hits = len(query_tokens & _tokens(body))
    return title_hits + body_hits


def find_related_articles(term: str, limit: int = RELATED_LIMIT) -> list[dict]:
    """Топ-N новостей сайта, релевантных термину. Источник — ЖИВАЯ коллекция
    media (news_dir клона), а не published.jsonl (тот не хранит тело и ≠ «на сайте»).

    Клон недоступен → [] (связанные новости — некритичный блок, не падаем).
    """
    from ..publishers.media_repo import clone_or_update_media, news_dir
    from ..utils.frontmatter import split_front_matter

    try:
        clone = clone_or_update_media()
        directory = news_dir(clone)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.warning("related: клон media недоступен (%s) — без связанных статей", exc)
        return []
    if not directory.exists():
        return []

    query_tokens = _tokens(term)
    scored: list[tuple[float, dict]] = []
    for md in directory.glob("*.md"):
        try:
            meta, body = split_front_matter(md.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — битый файл пропускаем
            continue
        score = _relevance(query_tokens, str(meta.get("title", "")), body)
        if score <= 0:
            continue
        scored.append((score, {
            "title": meta.get("title", ""),
            "slug": md.stem,
            "body_excerpt": body[:500],
            "pub_date": str(meta.get("pubDate", "")),
        }))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]


# --- Сборка bundle ---------------------------------------------------------

def gather_research(item: dict) -> dict:
    """research_bundle для Writer'а: {term, type, wikipedia, related_articles}."""
    term = item["term"]
    bundle = {
        "term": term,
        "type": item["type"],
        "wikipedia": get_wikipedia_summary(term),
        "related_articles": find_related_articles(term),
    }
    log.info("research «%s»: wiki=%s, related=%d", term,
             "есть" if bundle["wikipedia"] else "нет", len(bundle["related_articles"]))
    return bundle
