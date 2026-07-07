"""Тесты keyword scoring engine (перенос порогов из production-бота)."""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.scoring import calculate_relevance_score, passes_keyword_filter

KW = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "keywords.yaml").read_text(encoding="utf-8"))


def test_core_adtech_keyword_in_title():
    """programmatic в title → score ≥ 30 → проходит."""
    r = passes_keyword_filter("Programmatic spend jumps 20%", "", KW)
    assert r.score >= 30
    assert "programmatic" in r.matched


def test_excluded_topic_with_override():
    """sport ads + programmatic → override → проходит."""
    r = passes_keyword_filter(
        "Programmatic deal reshapes sport ads market", "DSP vendors line up", KW
    )
    assert r.score > 0
    assert r.excluded_by is None


def test_excluded_topic_without_override():
    """sport ads без override → score = 0 → отбрасывается."""
    r = calculate_relevance_score("New sport ads campaign for sneakers", "", KW)
    assert r.score == 0
    assert r.excluded_by == "sport ads"


def test_below_threshold():
    """Только generic_industry keyword → score < 20 → отбрасывается."""
    r = passes_keyword_filter("Company announces merger", "", KW)
    assert r.score == 0


def test_roundup_semicolons():
    """3+ точки с запятой в заголовке → news roundup → score = 0."""
    r = calculate_relevance_score("Google news; Meta update; Amazon ads; TikTok", "", KW)
    assert r.score == 0
    assert r.is_roundup


def test_word_boundaries():
    """«ads» не должно находиться внутри «roads»."""
    r = calculate_relevance_score("New roads planned in Texas", "", KW)
    assert "ads" not in r.matched


def test_title_and_desc_weights_sum():
    """Ключ и в title, и в desc — веса складываются (30+20 для core)."""
    r = calculate_relevance_score("CTV budgets grow", "CTV is eating linear TV", KW)
    assert r.score >= 50
