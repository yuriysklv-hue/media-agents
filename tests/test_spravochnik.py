"""Тесты пайплайна Базы знаний (Spravochnik).

LLM/сеть не задействуются: проверяются детерминированные части (очередь,
finalize-гейт, rules-QA, релевантность related, фактчек без Wikipedia, dry-run).
"""
from __future__ import annotations

import os

import pytest

from src.spravochnik import qa, queue_manager as qm
from src.spravochnik.writer import _clean_tags, _feedback_section, _finalize_meta


# --- queue_manager ---------------------------------------------------------

def _seed_queue(path):
    qm.save_queue([
        {"id": "adobe", "term": "Adobe", "type": "company", "status": "published",
         "iteration": 1, "slug": "adobe"},
        {"id": "ctv", "term": "CTV", "type": "term", "status": "pending",
         "iteration": 0, "slug": "ctv"},
        {"id": "iab", "term": "IAB", "type": "organization", "status": "revision",
         "iteration": 2, "feedback": "уточни год", "slug": "iab"},
    ], path)


def test_queue_roundtrip_and_next(tmp_path):
    p = tmp_path / "q.yaml"
    _seed_queue(p)
    items = qm.load_queue(p)
    assert [i["id"] for i in items] == ["adobe", "ctv", "iab"]
    # первый pending|revision в порядке очереди — ctv (adobe published)
    assert qm.get_next_item(items)["id"] == "ctv"


def test_update_status_writes_fields(tmp_path):
    p = tmp_path / "q.yaml"
    _seed_queue(p)
    qm.update_status("ctv", "review", path=p, pr_number=42, pr_url="http://x")
    item = qm.find_item("ctv", qm.load_queue(p))
    assert item["status"] == "review" and item["pr_number"] == 42


def test_add_item_unique_slug(tmp_path):
    p = tmp_path / "q.yaml"
    _seed_queue(p)
    added = qm.add_item("Adobe", "company", path=p)  # slug adobe уже занят
    assert added["slug"] == "adobe-2"
    assert added["status"] == "pending" and added["iteration"] == 0


def test_add_item_rejects_bad_type(tmp_path):
    p = tmp_path / "q.yaml"
    _seed_queue(p)
    with pytest.raises(ValueError):
        qm.add_item("X", "wrongtype", path=p)


# --- writer: finalize-гейт -------------------------------------------------

def test_finalize_sets_slug_type_pubdate(tmp_path):
    item = {"id": "ctv", "term": "CTV (Connected TV)", "type": "term", "slug": "ctv"}
    meta = _finalize_meta({"title": "CTV", "description": "d", "tags": ["a", "a", "b"]}, item)
    assert meta["type"] == "term" and meta["slug"] == "ctv"
    assert meta["pubDate"].endswith("Z") or "+" in meta["pubDate"] or "T" in meta["pubDate"]
    assert meta["tags"] == ["a", "b"]  # дедуп
    assert meta["related"] == [] and isinstance(meta["facts"], dict)


def test_finalize_description_trimmed_to_160():
    item = {"id": "x", "term": "X", "type": "term", "slug": "x"}
    long_desc = "Слово " * 60  # заведомо >160
    meta = _finalize_meta({"description": long_desc}, item)
    assert len(meta["description"]) <= 160


def test_clean_tags_caps_seven():
    assert _clean_tags([f"t{i}" for i in range(20)]) == [f"t{i}" for i in range(7)]


def test_feedback_section_empty_without_feedback():
    assert _feedback_section(None, 1) == ""
    assert "ИТЕРАЦИЯ 2" in _feedback_section("добавь X", 2)


# --- rules-QA --------------------------------------------------------------

def _good_company_meta():
    return {
        "title": "Adobe", "type": "company", "pubDate": "2026-07-19T12:00:00Z",
        "description": "Американская компания-разработчик ПО для контента и рекламы.",
        "tags": ["adtech", "platform", "software"],
        "facts": {"founded": 1982, "founders": ["Джон Уорнок"], "hq": "Сан-Хосе",
                  "official_url": "https://adobe.com"},
    }


_COMPANY_BODY = (
    "## О компании\n" + "текст " * 40 + "\n\n## История\n" + "текст " * 40 +
    "\n\n## Роль в рекламной индустрии\n" + "текст " * 40 +
    "\n\n## Ключевые продукты\n" + "текст " * 40
)


def test_qa_pass_valid_company():
    r = qa.check_rules(_good_company_meta(), _COMPANY_BODY, set(), "adobe")
    assert r.status == "PASS", r.errors


def test_qa_fail_missing_required_fact():
    meta = _good_company_meta()
    del meta["facts"]["founders"]
    r = qa.check_rules(meta, _COMPANY_BODY, set(), "adobe")
    assert r.status == "FAIL" and any("founders" in e for e in r.errors)


def test_qa_fail_description_too_long():
    meta = _good_company_meta()
    meta["description"] = "д" * 161
    r = qa.check_rules(meta, _COMPANY_BODY, set(), "adobe")
    assert r.status == "FAIL"


def test_qa_fail_missing_section():
    body = _COMPANY_BODY.replace("## История", "## Прочее")
    r = qa.check_rules(_good_company_meta(), body, set(), "adobe")
    assert r.status == "FAIL" and any("История" in e for e in r.errors)


def test_qa_fail_cjk_junk():
    body = _COMPANY_BODY + "\n\n保留 视频 指向"
    r = qa.check_rules(_good_company_meta(), body, set(), "adobe")
    assert r.status == "FAIL" and any("кодировка" in e for e in r.errors)


def test_qa_fail_thin_body():
    r = qa.check_rules(_good_company_meta(), "## О компании\nкоротко", set(), "adobe")
    assert r.status == "FAIL"


def test_qa_fail_slug_collision():
    r = qa.check_rules(_good_company_meta(), _COMPANY_BODY, {"adobe"}, "adobe")
    assert r.status == "FAIL" and any("занят" in e for e in r.errors)


def test_qa_fail_bad_type():
    meta = _good_company_meta()
    meta["type"] = "banana"
    r = qa.check_rules(meta, _COMPANY_BODY, set(), "adobe")
    assert r.status == "FAIL"


# --- fact_checker: без Wikipedia -------------------------------------------

def test_factcheck_no_wikipedia_passes_with_warning(tmp_path):
    from src.spravochnik.fact_checker import check_facts

    class _Store:
        pass
    draft = tmp_path / "x.md"
    draft.write_text("---\ntitle: X\nfacts: {}\n---\n\nтело", encoding="utf-8")
    res = check_facts(draft, {"wikipedia": None}, _Store())
    assert res.passed is True and res.warnings


# --- researcher: релевантность related -------------------------------------

def test_relevance_prioritises_title():
    from src.spravochnik.researcher import _relevance, _tokens

    q = _tokens("Criteo retail media")
    high = _relevance(q, "Criteo запускает retail media", "тело про рынок")
    low = _relevance(q, "Google обновил поиск", "criteo упомянут вскользь")
    assert high > low


# --- publisher: dry-run ----------------------------------------------------

def test_publish_dry_run_no_pr(tmp_path, monkeypatch):
    from src.spravochnik.publisher import publish

    monkeypatch.setenv("DRY_RUN", "true")
    draft = tmp_path / "ctv.md"
    draft.write_text("---\ntitle: CTV\ntype: term\n---\n\nтело", encoding="utf-8")
    res = publish({"id": "ctv", "term": "CTV", "type": "term", "slug": "ctv",
                   "iteration": 0}, draft)
    assert res.dry_run is True and res.pr_url is None
    monkeypatch.delenv("DRY_RUN", raising=False)
