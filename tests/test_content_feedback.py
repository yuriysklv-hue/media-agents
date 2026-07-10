"""Тесты правок по обратной связи: сноска Meta, обрезка description, текст-дедуп."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timezone

from src.processors.enricher import _fit_description
from src.utils.legal import add_restricted_org_footnotes
from src.utils.text_similarity import title_similarity
from src.writers.news_writer import _finalize_meta


# --- pubDate = момент выхода на 1screen, а не дата источника (задача 1b) ---

def _event(published_at="2026-07-06T10:30:00Z"):
    primary = {"source_name": "AdExchanger", "source_url": "https://adexchanger.com/x",
               "published_at": published_at, "is_primary": True}
    return {"event_id": "abc", "published_at": published_at, "sources": [primary]}, primary


def test_pubdate_is_now_not_source_date():
    event, primary = _event(published_at="2026-07-06T10:30:00Z")
    meta = {"title": "x", "pubDate": "2026-07-06T10:30:00Z"}
    _finalize_meta(meta, event, primary)
    pub = datetime.fromisoformat(meta["pubDate"].replace("Z", "+00:00"))
    # дата источника (06-07) не должна протекать в pubDate — ставится текущий UTC
    assert pub.date() != datetime(2026, 7, 6, tzinfo=timezone.utc).date()
    assert abs((datetime.now(timezone.utc) - pub).total_seconds()) < 120


def test_pubdate_not_in_future_for_qa():
    event, primary = _event()
    meta = {"title": "x"}
    _finalize_meta(meta, event, primary)
    pub = datetime.fromisoformat(meta["pubDate"].replace("Z", "+00:00"))
    # QA бракует pubDate в будущем (> now + 15 мин) — наш всегда в прошлом
    assert pub <= datetime.now(timezone.utc)


def test_finalize_sets_source_and_defaults():
    event, primary = _event()
    meta = {"title": "x"}
    _finalize_meta(meta, event, primary)
    assert meta["source"] == {"title": "AdExchanger", "url": "https://adexchanger.com/x"}
    assert meta["category"] == "adtech-world"
    assert meta["geo"] == ["МИР"]


# --- сноска о запрещённых организациях ---

def test_meta_footnote_added_once():
    body = "Meta объявила о новом формате. Позже Meta уточнила детали."
    out = add_restricted_org_footnotes(body)
    assert out.count("\\*") == 2  # один маркер у первого упоминания + один у сноски
    assert "Meta\\*" in out
    assert "экстремистской организацией" in out
    # второе упоминание Meta не помечается
    assert "Meta уточнила" in out


def test_footnote_idempotent():
    body = "Meta запускает инструмент."
    once = add_restricted_org_footnotes(body)
    twice = add_restricted_org_footnotes(once)
    assert once == twice


def test_no_footnote_without_mentions():
    body = "Google и Яндекс обновили рекламные кабинеты."
    assert add_restricted_org_footnotes(body) == body


def test_metaverse_not_matched():
    body = "Рынок Metaverse растёт, но данных мало."
    assert add_restricted_org_footnotes(body) == body


def test_footnote_lists_all_present_orgs():
    body = "Facebook и Instagram обновили ленту."
    out = add_restricted_org_footnotes(body)
    assert "Facebook\\*" in out
    assert "Instagram\\*" in out
    assert "Facebook и Instagram принадлежат Meta Platforms" in out


# --- обрезка description ---

def test_short_description_untouched():
    text = "Короткое описание в пределах лимита."
    assert _fit_description(text) == text


def test_description_cut_on_sentence_boundary():
    text = ("Meta оспаривает штраф в $1,4 трлн, почти равный капитализации компании. "
            "Дополнительный контекст, который в лимит уже не влезает и должен отсечься.")
    out = _fit_description(text, limit=160)
    assert len(out) <= 160
    assert out.endswith(".")       # закончили предложением
    assert "…" not in out          # без обрыва многоточием


def test_description_word_boundary_when_no_sentence():
    text = "адлкж " * 60  # длинный поток без знаков конца предложения
    out = _fit_description(text, limit=160)
    assert len(out) <= 160
    assert out.endswith("…")
    assert not out.endswith(" …")  # не режем посреди слова с висящим пробелом


def test_description_never_splits_word_midway():
    text = "Компания называет сумму необоснованной " + "и повторяет это раз за разом " * 10
    out = _fit_description(text, limit=160)
    assert len(out) <= 160
    # хвост — целое слово либо законченное предложение, не «необос»
    assert not out.rstrip("…").endswith("необос")


# --- текстовая близость заголовков ---

def test_near_identical_titles_high():
    a = "DuckDuckGo включил блокировку рекламы на YouTube по умолчанию"
    b = "DuckDuckGo по умолчанию блокирует рекламу YouTube"
    assert title_similarity(a, b) >= 0.62


def test_different_meta_stories_low():
    a = "Meta отвергает обвинения в намеренном продвижении рекламы детям"
    b = "Генпрокуроры 29 штатов оценили штраф для Meta в $1,4 трлн"
    assert title_similarity(a, b) < 0.62


def test_empty_titles_zero():
    assert title_similarity("", "что угодно") == 0.0
