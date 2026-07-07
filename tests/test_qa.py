"""Тесты rules-based QA (без LLM)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.processors.qa import check_rules

VALID_META = {
    "title": "Google запускает новую функцию DSP для рекламодателей США",  # 55 симв.
    "description": "Краткое описание новости для SEO, укладывается в лимит.",
    "pubDate": "2026-07-06T12:00:00Z",
    "author": "news-world",
    "category": "adtech-world",
    "geo": ["МИР"],
    "tags": ["programmatic", "DSP", "Google"],
    "source": {"title": "AdExchanger", "url": "https://www.adexchanger.com/x"},
}
VALID_BODY = "Новость о запуске. " * 40  # ~760 знаков


def _check(meta_patch=None, body=VALID_BODY, slugs=None, slug="google-dsp"):
    meta = {**VALID_META, **(meta_patch or {})}
    return check_rules(meta, body, slugs or set(), slug=slug, article_type="news")


def test_valid_frontmatter_passes():
    assert _check().status == "PASS"


def test_missing_required_field_fails():
    assert _check({"author": None}).status == "FAIL"


def test_short_title_fails():
    """title < 50 символов → FAIL."""
    assert _check({"title": "Коротко"}).status == "FAIL"


def test_ai_cliche_detected():
    """«почему это важно» в тексте → FAIL."""
    r = _check(body=VALID_BODY + " Почему это важно: рынок растёт.")
    assert r.status == "FAIL"
    assert any("анти-ИИ" in e for e in r.errors)


def test_long_description_fails():
    assert _check({"description": "х" * 161}).status == "FAIL"


def test_short_body_fails():
    """Тело < 500 знаков → брак."""
    assert _check(body="Слишком коротко.").status == "FAIL"


def test_future_pubdate_fails():
    assert _check({"pubDate": "2036-01-01T00:00:00Z"}).status == "FAIL"


def test_duplicate_slug_fails():
    assert _check(slugs={"google-dsp"}).status == "FAIL"


def test_missing_source_url_fails():
    assert _check({"source": {"title": "X", "url": "не-ссылка"}}).status == "FAIL"


def test_unknown_category_fails():
    assert _check({"category": "misc"}).status == "FAIL"


def test_tag_outside_vocabulary_is_warning_not_fail():
    r = _check({"tags": ["programmatic", "DSP", "неведомый-тег"]})
    assert r.status == "PASS"
    assert any("неведомый-тег" in w for w in r.warnings)
