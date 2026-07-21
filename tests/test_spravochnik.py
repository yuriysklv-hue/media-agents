"""Тесты пайплайна Базы знаний (Spravochnik).

LLM/сеть не задействуются: проверяются детерминированные части (очередь,
finalize-гейт, rules-QA, релевантность related, фактчек без Wikipedia, dry-run).
"""
from __future__ import annotations

import os

import pytest

from src.spravochnik import qa, queue_manager as qm
from src.spravochnik.writer import (
    _clean_tags,
    _feedback_section,
    _finalize_meta,
    _strip_factcheck_from_facts,
    _strip_unconfirmed_sentences,
)


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


# --- writer: вырезание неподтверждённых утверждений ------------------------

def test_strip_removes_unconfirmed_sentence_keeps_rest():
    # реальный кейс с Criteo, включая «ёлочки» из примера промпта
    body = (
        "Criteo — французская adtech-компания. "
        "В 2026 году появилась информация о покупке компании фондами "
        "{{fact_check: «дата не подтверждена»}}. "
        "Компания работает в сфере retail media."
    )
    out = _strip_unconfirmed_sentences(body)
    assert "fact_check" not in out and "{{" not in out
    assert "появилась информация" not in out
    assert "французская adtech-компания" in out
    assert "retail media" in out


def test_strip_noop_without_marker():
    body = "## О компании\nОбычный текст без маркеров.\n"
    assert _strip_unconfirmed_sentences(body) == body


def test_strip_handles_curly_quotes_and_case():
    body = "Факт один. Спорное утверждение {{Fact-Check: “нет данных”}}. Факт два."
    out = _strip_unconfirmed_sentences(body)
    assert "{{" not in out and "Спорное утверждение" not in out
    assert "Факт один." in out and "Факт два." in out


def test_strip_removes_multiple_and_collapses_empty_paragraph():
    body = (
        "## Определение\nТермин значит X.\n\n"
        "Только это спорно {{fact_check: \"нет данных\"}}.\n\n"
        "## Контекст\nA. Ещё спорно {{fact_check: \"?\"}}. B."
    )
    out = _strip_unconfirmed_sentences(body)
    assert "fact_check" not in out
    assert "\n\n\n" not in out  # опустевший абзац схлопнут
    assert "## Определение" in out and "## Контекст" in out
    assert "Термин значит X." in out and "A." in out and "B." in out


def test_strip_removes_unconfirmed_bullet():
    body = ("## Ключевые продукты\n- Продукт A\n"
            "- Слух про продукт B {{fact_check: \"не подтверждено\"}}\n- Продукт C")
    out = _strip_unconfirmed_sentences(body)
    assert "fact_check" not in out and "продукт B" not in out
    assert "Продукт A" in out and "Продукт C" in out


# --- writer: вычистка утёкшего маркера из facts (кейс advantage-plus) -------

def test_strip_factcheck_drops_marker_only_facts_field():
    facts = {
        "developer": "Meta",
        "launch_date": "{{fact_check: 'точная дата не подтверждена; ~2022'}}",
    }
    out = _strip_factcheck_from_facts(facts)
    assert "launch_date" not in out  # поле-маркер выброшено целиком
    assert out["developer"] == "Meta"


def test_strip_factcheck_keeps_confirmed_part_of_value():
    facts = {"category": "Автоматизация {{fact_check: \"нет данных\"}}"}
    out = _strip_factcheck_from_facts(facts)
    assert out["category"] == "Автоматизация"
    assert "{{" not in out["category"]


def test_strip_factcheck_cleans_list_values():
    facts = {"alternatives": ["Custom Algorithms",
                              "{{fact_check: 'слух'}}", "Яндекс.Директ"]}
    out = _strip_factcheck_from_facts(facts)
    assert out["alternatives"] == ["Custom Algorithms", "Яндекс.Директ"]


def test_finalize_strips_factcheck_from_facts():
    item = {"id": "ap", "term": "Advantage+", "type": "technology", "slug": "advantage-plus"}
    meta = _finalize_meta(
        {"description": "d", "facts": {
            "developer": "Meta", "category": "Автоматизация рекламы",
            "official_url": "https://facebook.com/business/advantage-plus",
            "launch_date": "{{fact_check: 'дата не подтверждена'}}"}},
        item,
    )
    assert "launch_date" not in meta["facts"]
    assert "{{" not in str(meta["facts"])


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


def test_qa_fail_marker_leak_in_body():
    body = _COMPANY_BODY + "\n\nСпорный факт {{fact_check: \"нет данных\"}}."
    r = qa.check_rules(_good_company_meta(), body, set(), "adobe")
    assert r.status == "FAIL" and any("маркер" in e for e in r.errors)


def test_qa_fail_marker_leak_in_facts():
    meta = _good_company_meta()
    meta["facts"]["founded"] = "{{fact_check: 'год не подтверждён'}}"
    r = qa.check_rules(meta, _COMPANY_BODY, set(), "adobe")
    assert r.status == "FAIL" and any("маркер" in e for e in r.errors)


def test_qa_fail_restricted_org_without_footnote():
    body = _COMPANY_BODY + "\n\nИнструмент разработан компанией Meta."
    r = qa.check_rules(_good_company_meta(), body, set(), "adobe")
    assert r.status == "FAIL" and any("запрещённая" in e for e in r.errors)


def test_qa_pass_restricted_org_with_footnote():
    from src.utils.legal import add_restricted_org_footnotes
    body = add_restricted_org_footnotes(
        _COMPANY_BODY + "\n\nИнструмент разработан компанией Meta.")
    r = qa.check_rules(_good_company_meta(), body, set(), "adobe")
    assert r.status == "PASS", r.errors


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
