"""Тесты Phase 3a: российские источники (keywords_ru, детерминизм региона)."""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.processors.enricher import enrich_draft
from src.utils.frontmatter import render_markdown
from src.utils.scoring import calculate_relevance_score, passes_keyword_filter
from src.utils.state import StateManager
from src.writers.news_writer import _finalize_meta

ROOT = Path(__file__).resolve().parents[1]
KW_RU = yaml.safe_load((ROOT / "config" / "keywords_ru.yaml").read_text(encoding="utf-8"))


# --- keywords_ru: русский скоринг ---

def test_ru_core_keyword_in_title():
    """«программатик» в заголовке → score ≥ 30 → проходит порог."""
    r = passes_keyword_filter("Программатик в России вырос на 30%", "", KW_RU)
    assert r.score >= 30
    assert "программатик" in r.matched


def test_ru_platform_in_title_passes_threshold():
    """Один бренд-платформа в заголовке (Яндекс, 20) = порог 20 → проходит."""
    r = passes_keyword_filter("Яндекс обновил кабинет для брендов", "", KW_RU)
    assert r.score >= 20


def test_ru_exclusion_without_override():
    """«казино» без override → score = 0 → отбрасывается."""
    r = calculate_relevance_score("Реклама казино снова под вопросом", "", KW_RU)
    assert r.score == 0
    assert r.excluded_by == "казино"


def test_ru_exclusion_with_override():
    """«казино» + «программатик» → override → материал проходит."""
    r = calculate_relevance_score("Программатик и казино: разбор рынка", "", KW_RU)
    assert r.score > 0
    assert r.excluded_by is None


def test_ru_generic_alone_below_threshold():
    """Только generic-слово (слияние) → score < 20 → отбрасывается."""
    r = passes_keyword_filter("Компания объявила слияние", "", KW_RU)
    assert r.score == 0


# --- детерминизм региона в Enricher (LLM локально недоступен → фолбэк) ---

def _draft(tmp_path: Path, meta: dict, body: str = "Текст новости. " * 50) -> Path:
    path = tmp_path / "draft-ru1.md"
    path.write_text(render_markdown(meta, body), encoding="utf-8")
    return path


def test_enrich_ru_region_deterministic(tmp_path):
    """region=ru → category adtech-ru, geo [РФ], author news-ru — без LLM."""
    state = StateManager(data_dir=tmp_path / "data")
    meta = {"title": "Яндекс Реклама запускает новый формат медийной рекламы",
            "source": {"title": "Яндекс Реклама", "url": "https://yandex.ru/adv/news/x"}}
    out = enrich_draft(_draft(tmp_path, meta), state, article_type="news", region="ru")
    result = yaml.safe_load(out.read_text(encoding="utf-8").split("---")[1])
    assert result["category"] == "adtech-ru"
    assert result["geo"] == ["РФ"]
    assert result["author"] == "news-ru"


def test_enrich_ru_author_overrides_category_map(tmp_path):
    """Даже market-news от ru-источника выходит от news-ru, не news-world."""
    state = StateManager(data_dir=tmp_path / "data")
    meta = {"title": "Российский adtech-холдинг закрыл сделку по поглощению",
            "category": "market-news",
            "source": {"title": "АКАР", "url": "https://akarussia.ru/x"}}
    out = enrich_draft(_draft(tmp_path, meta), state, article_type="news", region="ru")
    result = yaml.safe_load(out.read_text(encoding="utf-8").split("---")[1])
    assert result["author"] == "news-ru"
    assert result["geo"] == ["РФ"]


def test_enrich_world_region_unchanged(tmp_path):
    """region=world → прежнее поведение: adtech-world / МИР / news-world."""
    state = StateManager(data_dir=tmp_path / "data")
    meta = {"title": "Google rolls out new programmatic tools for advertisers",
            "source": {"title": "AdExchanger", "url": "https://adexchanger.com/x"}}
    out = enrich_draft(_draft(tmp_path, meta), state, article_type="news", region="world")
    result = yaml.safe_load(out.read_text(encoding="utf-8").split("---")[1])
    assert result["category"] == "adtech-world"
    assert result["geo"] == ["МИР"]
    assert result["author"] == "news-world"


# --- сид региона в райтере (_finalize_meta) ---

def test_finalize_ru_region_seeds_rf():
    event = {"event_id": "x", "region": "ru", "published_at": "2026-07-10T10:00:00Z",
             "sources": [{"source_name": "АКАР", "source_url": "https://akarussia.ru/x",
                          "is_primary": True}]}
    primary = event["sources"][0]
    meta = {"title": "x"}
    _finalize_meta(meta, event, primary)
    assert meta["category"] == "adtech-ru"
    assert meta["geo"] == ["РФ"]


def test_finalize_world_region_seeds_mir():
    event = {"event_id": "x", "region": "world", "published_at": "2026-07-10T10:00:00Z",
             "sources": [{"source_name": "Digiday", "source_url": "https://digiday.com/x",
                          "is_primary": True}]}
    primary = event["sources"][0]
    meta = {"title": "x"}
    _finalize_meta(meta, event, primary)
    assert meta["category"] == "adtech-world"
    assert meta["geo"] == ["МИР"]
