"""Тесты тест-режима (PIPELINE_TEST_MODE): кап объёма в pre_filter, ранжирование.

Коллектор (обход дедупа, окно свежести, несохранение seen) требует feedparser
и проверяется в CI / боевом тест-прогоне — здесь покрыт pre-filter кап,
детерминированный и запускаемый локально.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.processors import pre_filter as pf


class _Score:
    def __init__(self, s):
        self.score = s
        self.matched = ["adtech"]


def test_relevance_prefers_llm_then_keyword():
    # LLM-скор важнее keyword-скора
    assert pf._relevance({"llm_score": 8, "keyword_score": 10}) > \
           pf._relevance({"llm_score": 7, "keyword_score": 100})
    # при равном/отсутствующем LLM — по keyword
    assert pf._relevance({"llm_score": None, "keyword_score": 30}) > \
           pf._relevance({"llm_score": None, "keyword_score": 20})
    # любой оценённый GLM важнее неоценённого (llm_score None → в хвост)
    assert pf._relevance({"llm_score": 0, "keyword_score": 0}) > \
           pf._relevance({"llm_score": None, "keyword_score": 999})


def _setup(tmp_path, monkeypatch, n_items):
    items = [{"id": f"i{k}", "title": "t", "summary": "s", "language": "en"} for k in range(n_items)]
    monkeypatch.setattr(pf, "RAW_PATH", tmp_path / "raw.jsonl")
    monkeypatch.setattr(pf, "PASSED_PATH", tmp_path / "passed.jsonl")
    pf.write_jsonl(pf.RAW_PATH, items)
    monkeypatch.setattr(pf, "passes_keyword_filter", lambda title, summary, cfg: _Score(10))
    # каждому свой GLM-скор 0..n-1 (детерминированный топ)
    monkeypatch.setattr(pf, "_llm_scores",
                        lambda survivors, state: {it["id"]: {"score": k, "reason": "r"}
                                                  for k, it in enumerate(survivors)})

    class _State:
        def get_last_run(self, *a):
            return None

    return _State()


def test_pre_filter_caps_to_test_max_items(tmp_path, monkeypatch):
    state = _setup(tmp_path, monkeypatch, n_items=40)
    monkeypatch.setenv("PIPELINE_TEST_MODE", "1")
    monkeypatch.setenv("TEST_MAX_ITEMS", "5")

    res = pf.run_pre_filter(state)
    assert res.passed == 5  # твёрдый потолок объёма в тест-режиме


def test_pre_filter_no_cap_without_test_mode(tmp_path, monkeypatch):
    state = _setup(tmp_path, monkeypatch, n_items=40)
    monkeypatch.delenv("PIPELINE_TEST_MODE", raising=False)

    res = pf.run_pre_filter(state)
    # без тест-режима капа нет: проходят все с GLM-скором >= порога (5..39 = 35 шт.)
    assert res.passed == 35
