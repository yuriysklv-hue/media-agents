"""Тесты rules-based QA (без LLM)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.processors.qa import check_quote_markup, check_rules

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


def _check_digest(title, existing=None):
    meta = {
        "title": title,
        "description": "Дайджест недели: краткое описание для SEO в пределах лимита.",
        "pubDate": "2026-07-06T12:00:00Z",
        "author": "news-world",
        "category": "adtech-world",
        "geo": ["МИР"],
        "tags": ["weekly-digest", "retail-media", "google"],
        "week": "2026-W29",
    }
    body = "Главное за неделю. " * 200  # ~3800 знаков (digest min 2000)
    return check_rules(meta, body, existing or set(), slug="2026-w29", article_type="digest")


def test_digest_title_82_passes():
    """82 символа: у новости FAIL (50-80), у дайджеста PASS (50-100)."""
    t82 = "PayPal доказывает ROI retail media, а атрибуция уходит в кризис доверия всего рынка"
    assert 80 < len(t82) <= 100, len(t82)
    assert _check({"title": t82}).status == "FAIL"      # news
    assert _check_digest(t82).status == "PASS"           # digest


def test_digest_title_over_100_fails():
    assert _check_digest("Слово " * 25).status == "FAIL"  # >100 симв.


def test_digest_slug_reissue_allowed():
    """Дайджест переиздаётся под slug=неделя → занятый slug НЕ бракуется."""
    title = "Retail media обгоняет соцсети, а Google снова тянет с отказом от cookie"
    assert _check_digest(title, existing={"2026-w29"}).status == "PASS"


def test_news_slug_duplicate_still_fails():
    """У новости совпадение slug с опубликованным — по-прежнему FAIL."""
    assert _check(slugs={"google-dsp"}, slug="google-dsp").status == "FAIL"


def test_additional_sources_valid_passes():
    extras = [{"title": "Digiday", "url": "https://digiday.com/x"}]
    assert _check({"additional_sources": extras}).status == "PASS"


def test_additional_sources_bad_url_fails():
    extras = [{"title": "Digiday", "url": "not-a-url"}]
    assert _check({"additional_sources": extras}).status == "FAIL"


def test_additional_sources_missing_title_fails():
    extras = [{"url": "https://digiday.com/x"}]
    assert _check({"additional_sources": extras}).status == "FAIL"


def test_additional_sources_duplicating_primary_fails():
    extras = [{"title": "AdExchanger", "url": "https://www.adexchanger.com/x"}]  # = primary
    assert _check({"additional_sources": extras}).status == "FAIL"


def test_additional_sources_empty_list_fails():
    assert _check({"additional_sources": []}).status == "FAIL"


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


# --- Разметка цитат <q cite> (задача 7-E, формат B) ---

def test_wellformed_quote_markup_no_warning():
    body = 'Текст. «<q cite="https://ex.com">так и есть</q>», — сказал X. ' * 5
    assert check_quote_markup(body) == []


def test_unbalanced_quote_tags_warns():
    body = '«<q cite="https://ex.com">без закрытия'
    assert any("не сбалансиров" in w for w in check_quote_markup(body))


def test_quote_without_cite_warns():
    body = "«<q>нет источника</q>», — заметил Y."
    assert any("без cite" in w for w in check_quote_markup(body))


def test_split_quote_two_fragments_ok():
    body = ('«<q cite="https://ex.com">Я проповедую обращённым,</q> — признала она. '
            '— <q cite="https://ex.com">если не внедрят, ничего не изменится</q>».')
    assert check_quote_markup(body) == []


def test_malformed_quote_markup_is_warning_not_fail():
    """Кривой <q> не роняет материал — только warning (сборку Astro не ломает)."""
    r = _check(body=VALID_BODY + ' «<q>без источника и закрытия')
    assert r.status == "PASS"
    assert any("разметка цитат" in w for w in r.warnings)
