"""RSS Collector: сбор статей из фидов, извлечение полного текста, дедуп по URL."""
from __future__ import annotations

import hashlib
import html
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import feedparser

from ..utils.config import DATA_DIR
from ..utils.logger import get_logger
from ..utils.state import StateManager, append_jsonl, utcnow_iso

log = get_logger("collector.rss")

USER_AGENT = "MediaAgents/1.0 (+https://1screen.ru)"
FETCH_TIMEOUT = 15  # секунд на выкачивание полного текста
MIN_SUMMARY_CHARS = 500  # короче — идём за полным текстом через trafilatura

RAW_ITEMS_PATH = DATA_DIR / "inbox" / "raw_items.jsonl"

_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class CollectResult:
    added: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def item_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _clean_html(text: str) -> str:
    return html.unescape(_TAG_RE.sub(" ", text or "")).strip()


def _published_iso(entry) -> str:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    return utcnow_iso()


def _fetch_full_text(url: str) -> str | None:
    """Полный текст статьи через trafilatura; None — не смогли извлечь."""
    try:
        import trafilatura

        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        return trafilatura.extract(downloaded, include_links=True)
    except Exception as exc:  # сеть/парсинг — не валим пайплайн
        log.warning("trafilatura не извлёк %s: %s", url, exc)
        return None


def collect_rss(sources_config: dict, state: StateManager) -> CollectResult:
    """Собирает включённые фиды в data/inbox/raw_items.jsonl (append-only)."""
    result = CollectResult()
    seen = state.load_seen_urls()

    for feed_cfg in sources_config.get("rss_feeds", []):
        if not feed_cfg.get("enabled", True):
            continue
        name, url = feed_cfg["name"], feed_cfg["url"]
        log.info("фид %s: %s", name, url)
        try:
            parsed = feedparser.parse(url, agent=USER_AGENT)
            if parsed.bozo and not parsed.entries:
                raise RuntimeError(parsed.get("bozo_exception", "пустой ответ"))
        except Exception as exc:
            log.warning("фид %s недоступен: %s — пропускаем", name, exc)
            result.errors.append(f"{name}: {exc}")
            continue

        for entry in parsed.entries:
            link = entry.get("link", "").strip()
            if not link or link in seen:
                result.skipped += 1
                continue

            title = _clean_html(entry.get("title", ""))
            summary = _clean_html(entry.get("summary", "") or entry.get("description", ""))

            content = summary
            content_extracted = False
            if len(summary) < MIN_SUMMARY_CHARS:
                full = _fetch_full_text(link)
                if full:
                    content = full
                    content_extracted = True

            raw_item = {
                "id": item_id(link),
                "source_type": "rss",
                "source_name": name,
                "source_url": link,
                "title": title,
                "summary": summary,
                "content": content,
                "content_extracted": content_extracted,
                "language": feed_cfg.get("language", "en"),
                "region": feed_cfg.get("region", "world"),
                "published_at": _published_iso(entry),
                "collected_at": utcnow_iso(),
            }
            append_jsonl(RAW_ITEMS_PATH, raw_item)
            seen.add(link)
            result.added += 1

    state.save_seen_urls(seen)
    log.info("сбор завершён: +%d, пропущено %d, ошибок %d",
             result.added, result.skipped, len(result.errors))
    return result
