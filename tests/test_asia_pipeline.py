"""Тесты Азии: keywords_asia, выбор словаря по региону, детерминизм региона."""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.processors.enricher import enrich_draft
from src.processors.pre_filter import select_keywords
from src.utils.frontmatter import render_markdown
from src.utils.scoring import calculate_relevance_score, passes_keyword_filter
from src.utils.state import StateManager
from src.writers.news_writer import _finalize_meta

ROOT = Path(__file__).resolve().parents[1]
KW_ASIA = yaml.safe_load((ROOT / "config" / "keywords_asia.yaml").read_text(encoding="utf-8"))


# --- keywords_asia: скоринг ---

def test_asia_platform_in_title_passes_threshold():
    """Одна платформа в заголовке (Alibaba, 20) ≥ порога 15 → проходит."""
    r = passes_keyword_filter("Alibaba expands Taobao ad tools for merchants", "", KW_ASIA)
    assert r.score >= 20
    assert r.score >= KW_ASIA["score_threshold"]


def test_asia_core_keyword_in_title():
    """core-термин (retail media, 30) в заголовке → проходит с запасом."""
    r = passes_keyword_filter("Retail media booms across Southeast Asia", "", KW_ASIA)
    assert r.score >= 30


def test_asia_exclusion_without_override():
    """«casino» без override → score = 0 → отбрасывается."""
    r = calculate_relevance_score("Casino ads spread on regional apps", "", KW_ASIA)
    assert r.score == 0
    assert r.excluded_by == "casino"


def test_asia_exclusion_with_override():
    """«casino» + «programmatic» → override → проходит."""
    r = calculate_relevance_score("Programmatic and casino ads: a breakdown", "", KW_ASIA)
    assert r.score > 0
    assert r.excluded_by is None


def test_asia_generic_alone_below_threshold():
    """Только generic-слово → ниже порога 15 → отбрасывается."""
    r = passes_keyword_filter("Company announces a merger", "", KW_ASIA)
    assert r.score < KW_ASIA["score_threshold"]


# --- выбор словаря в pre_filter: по региону, не по языку ---

def test_select_keywords_asia_by_region():
    """asia приходит на en, но словарь выбирается по region, не по языку."""
    dicts = {"en": {"en": True}, "ru": {"ru": True}, "asia": {"asia": True}}
    item = {"language": "en", "region": "asia"}
    assert select_keywords(item, dicts) is dicts["asia"]


def test_select_keywords_world_stays_en():
    dicts = {"en": {"en": True}, "ru": {"ru": True}, "asia": {"asia": True}}
    assert select_keywords({"language": "en", "region": "world"}, dicts) is dicts["en"]


def test_select_keywords_ru_by_language():
    dicts = {"en": {"en": True}, "ru": {"ru": True}, "asia": {"asia": True}}
    assert select_keywords({"language": "ru", "region": "ru"}, dicts) is dicts["ru"]


# --- детерминизм региона в Enricher (LLM локально недоступен → фолбэк) ---

def _draft(tmp_path: Path, meta: dict, body: str = "News text. " * 60) -> Path:
    path = tmp_path / "draft-asia1.md"
    path.write_text(render_markdown(meta, body), encoding="utf-8")
    return path


def test_enrich_asia_region_deterministic(tmp_path):
    """region=asia → category adtech-asia, geo [АЗИЯ], author news-asia — без LLM."""
    state = StateManager(data_dir=tmp_path / "data")
    meta = {"title": "Alibaba rolls out new Alimama ad formats for Tmall brands",
            "source": {"title": "Alizila", "url": "https://www.alizila.com/x"}}
    out = enrich_draft(_draft(tmp_path, meta), state, article_type="news", region="asia")
    result = yaml.safe_load(out.read_text(encoding="utf-8").split("---")[1])
    assert result["category"] == "adtech-asia"
    assert result["geo"] == ["АЗИЯ"]
    assert result["author"] == "news-asia"


def test_enrich_asia_author_overrides_category_map(tmp_path):
    """Даже market-news от asia-источника выходит от news-asia, не news-world."""
    state = StateManager(data_dir=tmp_path / "data")
    meta = {"title": "Asian adtech holding closes an acquisition deal this quarter",
            "category": "market-news",
            "source": {"title": "Alizila", "url": "https://www.alizila.com/y"}}
    out = enrich_draft(_draft(tmp_path, meta), state, article_type="news", region="asia")
    result = yaml.safe_load(out.read_text(encoding="utf-8").split("---")[1])
    assert result["author"] == "news-asia"
    assert result["geo"] == ["АЗИЯ"]


# --- сид региона в райтере (_finalize_meta) ---

def test_finalize_asia_region_seeds_asia():
    event = {"event_id": "x", "region": "asia", "published_at": "2026-07-18T10:00:00Z",
             "sources": [{"source_name": "Alizila", "source_url": "https://www.alizila.com/x",
                          "is_primary": True}]}
    primary = event["sources"][0]
    meta = {"title": "x"}
    _finalize_meta(meta, event, primary)
    assert meta["category"] == "adtech-asia"
    assert meta["geo"] == ["АЗИЯ"]
