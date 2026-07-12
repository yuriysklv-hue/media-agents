"""Тесты RSS-коллектора: фильтр свежести (30 дней) и парсинг даты записи."""
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collectors.rss_collector import MAX_AGE_DAYS, _entry_dt, _is_recent

CUTOFF = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)


def _entry(days_ago: float) -> dict:
    return {"published_parsed": time.gmtime(time.time() - days_ago * 86400)}


def test_fresh_entry_kept():
    assert _is_recent(_entry(5), CUTOFF) is True


def test_old_entry_dropped():
    """Старше месяца — не берём (архивный хвост фида)."""
    assert _is_recent(_entry(40), CUTOFF) is False


def test_boundary_recent_kept():
    assert _is_recent(_entry(MAX_AGE_DAYS - 1), CUTOFF) is True


def test_dateless_entry_kept():
    """Запись без даты считаем свежей (не теряем материал)."""
    assert _is_recent({}, CUTOFF) is True


def test_entry_dt_parses_utc():
    dt = _entry_dt(_entry(1))
    assert dt is not None and dt.tzinfo is not None


def test_entry_dt_none_without_date():
    assert _entry_dt({}) is None
