"""Keyword scoring engine — перенос из production-бота (hybrid_generator.py).

Четырёхуровневая система взвешенных ключевых слов + exclusions + override.
Пороги валидированы в production: 67-85% эффективности фильтрации.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SCORE_LEVELS = ("core_adtech", "platforms", "advertising_general", "generic_industry")


@dataclass
class ScoreResult:
    score: int
    matched: list[str] = field(default_factory=list)
    excluded_by: str | None = None
    is_roundup: bool = False

    @property
    def passed_threshold(self) -> bool:
        return self.score > 0


def _keyword_pattern(keyword: str) -> re.Pattern:
    # Границы слова, чтобы «ads» не находилось внутри «roads».
    return re.compile(r"(?<!\w)" + re.escape(keyword.lower()) + r"(?!\w)")


def _contains(text: str, keyword: str) -> bool:
    return bool(_keyword_pattern(keyword).search(text))


def calculate_relevance_score(title: str, summary: str, keywords_config: dict) -> ScoreResult:
    """Скор по title+summary. score=0 — материал отбрасывается до LLM."""
    title_l = (title or "").lower()
    summary_l = (summary or "").lower()
    full = f"{title_l} {summary_l}"

    # News roundup: 3+ точки с запятой в заголовке.
    threshold = keywords_config.get("roundup_semicolon_threshold", 3)
    if title_l.count(";") >= threshold:
        return ScoreResult(score=0, is_roundup=True)

    # Exclusions с перепроверкой сильными adtech-ключами.
    for topic in keywords_config.get("exclusions", []):
        if _contains(full, topic):
            overrides = keywords_config.get("override_keywords", [])
            if not any(_contains(full, kw) for kw in overrides):
                return ScoreResult(score=0, excluded_by=topic)
            break  # override сработал — считаем скор как обычно

    score = 0
    matched: list[str] = []
    for level in SCORE_LEVELS:
        level_cfg = keywords_config.get(level)
        if not level_cfg:
            continue
        w_title = level_cfg["weight_in_title"]
        w_desc = level_cfg["weight_in_desc"]
        for keyword in level_cfg["keywords"]:
            in_title = _contains(title_l, keyword)
            in_desc = _contains(summary_l, keyword)
            if in_title:
                score += w_title
            if in_desc:
                score += w_desc
            if in_title or in_desc:
                matched.append(keyword)

    return ScoreResult(score=score, matched=matched)


def passes_keyword_filter(title: str, summary: str, keywords_config: dict) -> ScoreResult:
    """Скоринг + порог score_threshold. score обнуляется, если порог не пройден."""
    result = calculate_relevance_score(title, summary, keywords_config)
    threshold = keywords_config.get("score_threshold", 20)
    if 0 < result.score < threshold:
        result.score = 0
    return result
