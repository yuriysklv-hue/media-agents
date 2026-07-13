"""RSS Collector: сбор статей из фидов, извлечение полного текста, дедуп по URL."""
from __future__ import annotations

import hashlib
import html
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import feedparser

from ..utils.config import DATA_DIR, env_flag
from ..utils.logger import get_logger
from ..utils.state import StateManager, append_jsonl, utcnow_iso

log = get_logger("collector.rss")

USER_AGENT = "MediaAgents/1.0 (+https://1screen.ru)"
FETCH_TIMEOUT = 15  # секунд на выкачивание полного текста
FEED_FETCH_TIMEOUT = 20  # секунд на скачивание самого фида (feedparser сам таймаут не держит)
MIN_SUMMARY_CHARS = 500  # короче — идём за полным текстом через trafilatura
MAX_AGE_DAYS = 30  # значение max_age_days по умолчанию для RU-фидов (в sources.yaml)
MAX_ENTRIES_PER_FEED = 120  # страховка от фида-архива, отдающего тысячи записей

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


def _entry_dt(entry) -> datetime | None:
    """Дата публикации записи как datetime (UTC); None — даты нет."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
    return None


def _published_iso(entry) -> str:
    dt = _entry_dt(entry)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else utcnow_iso()


def _is_recent(entry, cutoff: datetime) -> bool:
    """Свежая ли запись (моложе cutoff). Запись без даты считаем свежей."""
    dt = _entry_dt(entry)
    return dt is None or dt >= cutoff


def _parse_feed(url: str):
    """Скачивает фид с таймаутом и парсит из байтов.

    feedparser.parse(url) сам таймаут не держит: зависший хост (наблюдали у
    yandex.ru/adv/news/rss ~38 мин) подвешивает весь этап collect до отмены
    воркфлоу по timeout-minutes. Качаем через requests с таймаутом, парсим байты.
    """
    import requests

    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=FEED_FETCH_TIMEOUT)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


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
    """Собирает включённые фиды в data/inbox/raw_items.jsonl (append-only).

    Тест-режим (PIPELINE_TEST_MODE): (1) память дедупа игнорируется, чтобы набрать
    материал для теста; но чтобы не утащить весь архив фидов, (2) ко ВСЕМ фидам
    принудительно применяется окно свежести TEST_MAX_AGE_DAYS (по умолчанию 3 дня),
    и (3) seen_urls НЕ сохраняется — боевая память дедупа в main не трогается.
    """
    result = CollectResult()
    test_mode = env_flag("PIPELINE_TEST_MODE")
    test_max_age = int(os.environ.get("TEST_MAX_AGE_DAYS", "3"))
    # В тест-режиме память игнорируем (иначе ветка наследует seen из main и собирает 0).
    seen = set() if test_mode else state.load_seen_urls()
    if test_mode:
        log.warning("ТЕСТ-РЕЖИМ: дедуп-память игнорируется, окно свежести %d дн. на все фиды, "
                    "seen_urls НЕ сохраняется", test_max_age)

    for feed_cfg in sources_config.get("rss_feeds", []):
        if not feed_cfg.get("enabled", True):
            continue
        name, url = feed_cfg["name"], feed_cfg["url"]
        # Фильтр свежести — только для фидов с max_age_days (RU-первоисточники с
        # архивным хвостом). Мировые фиды короткие, идут без ограничения — их
        # поведение не трогаем. В тест-режиме окно принудительно на ВСЕ фиды
        # (защита от архивного хвоста при выключенной памяти).
        max_age = test_max_age if test_mode else feed_cfg.get("max_age_days")
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age) if max_age else None
        log.info("фид %s: %s", name, url)
        try:
            parsed = _parse_feed(url)
            if parsed.bozo and not parsed.entries:
                raise RuntimeError(parsed.get("bozo_exception", "пустой ответ"))
        except Exception as exc:
            log.warning("фид %s недоступен: %s — пропускаем", name, exc)
            result.errors.append(f"{name}: {exc}")
            continue

        added_here = 0
        for entry in parsed.entries[:MAX_ENTRIES_PER_FEED]:
            link = entry.get("link", "").strip()
            if not link or link in seen:
                result.skipped += 1
                continue

            if cutoff and not _is_recent(entry, cutoff):
                # первичный скрининг: старше max_age_days не берём (архивный хвост)
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
            added_here += 1
        if max_age:
            log.info("фид %s: +%d свежих (за %d дней)", name, added_here, max_age)
        else:
            log.info("фид %s: +%d", name, added_here)

    if not test_mode:  # тест-режим боевую память дедупа не трогает
        state.save_seen_urls(seen)
    log.info("сбор завершён: +%d, пропущено %d, ошибок %d",
             result.added, result.skipped, len(result.errors))
    return result
