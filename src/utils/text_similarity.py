"""Текстовая близость заголовков — фолбэк-дедуп, когда эмбеддинги недоступны.

На z.ai эмбеддинги отключены, поэтому семантический дедуп деградирует. Здесь —
дешёвая детерминированная замена: token-Jaccard + посимвольный SequenceMatcher по
русским заголовкам. Цель — ловить одну и ту же новость из разных RSS-фидов
(почти совпадающие заголовки), не склеивая при этом разные сюжеты про один бренд.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_WORD_RE = re.compile(r"\w+", re.UNICODE)
# Служебные слова, не несущие смысла для сравнения заголовков.
_STOP = {
    "и", "в", "во", "на", "по", "с", "со", "за", "от", "до", "для", "о", "об",
    "из", "к", "у", "а", "но", "что", "как", "же", "the", "a", "an", "of", "to",
    "in", "on", "for", "and", "with",
}


_STEM_LEN = 5  # префикс токена — грубый стемминг под русскую словоизменительность


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD_RE.findall(text.lower()) if t not in _STOP and len(t) > 1}


def _stems(tokens: set[str]) -> set[str]:
    return {t[:_STEM_LEN] for t in tokens}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if (a and b) else 0.0


def title_similarity(a: str, b: str) -> float:
    """0..1: близость двух заголовков.

    Token-Jaccard (по словам и по 5-буквенным основам — чтобы «блокирует» ≈
    «блокировку»), усиленный посимвольным SequenceMatcher. Ловит одну новость из
    разных фидов, но не склеивает разные сюжеты про один бренд.
    """
    if not a or not b:
        return 0.0
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    jaccard = max(_jaccard(ta, tb), _jaccard(_stems(ta), _stems(tb)))
    ratio = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return 0.6 * jaccard + 0.4 * ratio
