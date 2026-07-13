"""Тесты правок по обратной связи: сноска Meta, обрезка description, текст-дедуп."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timezone

from src.processors.enricher import _fit_description
from src.utils.frontmatter import split_front_matter
from src.utils.legal import add_restricted_org_footnotes
from src.utils.text_similarity import title_similarity
from src.writers import news_writer as nw
from src.writers.news_writer import _finalize_meta, _retry_hint, _title_len_ok


# --- pubDate = момент выхода на 1screen, а не дата источника (задача 1b) ---

def _event(published_at="2026-07-06T10:30:00Z"):
    primary = {"source_name": "AdExchanger", "source_url": "https://adexchanger.com/x",
               "published_at": published_at, "is_primary": True}
    return {"event_id": "abc", "published_at": published_at, "sources": [primary]}, primary


def test_pubdate_is_now_not_source_date():
    event, primary = _event(published_at="2026-07-06T10:30:00Z")
    meta = {"title": "x", "pubDate": "2026-07-06T10:30:00Z"}
    _finalize_meta(meta, event, primary)
    pub = datetime.fromisoformat(meta["pubDate"].replace("Z", "+00:00"))
    # дата источника (06-07) не должна протекать в pubDate — ставится текущий UTC
    assert pub.date() != datetime(2026, 7, 6, tzinfo=timezone.utc).date()
    assert abs((datetime.now(timezone.utc) - pub).total_seconds()) < 120


def test_pubdate_not_in_future_for_qa():
    event, primary = _event()
    meta = {"title": "x"}
    _finalize_meta(meta, event, primary)
    pub = datetime.fromisoformat(meta["pubDate"].replace("Z", "+00:00"))
    # QA бракует pubDate в будущем (> now + 15 мин) — наш всегда в прошлом
    assert pub <= datetime.now(timezone.utc)


def test_finalize_sets_source_and_defaults():
    event, primary = _event()
    meta = {"title": "x"}
    _finalize_meta(meta, event, primary)
    assert meta["source"] == {"title": "AdExchanger", "url": "https://adexchanger.com/x"}
    assert meta["category"] == "adtech-world"
    assert meta["geo"] == ["МИР"]


# --- сноска о запрещённых организациях ---

def test_meta_footnote_added_once():
    body = "Meta объявила о новом формате. Позже Meta уточнила детали."
    out = add_restricted_org_footnotes(body)
    assert out.count("\\*") == 2  # один маркер у первого упоминания + один у сноски
    assert "Meta\\*" in out
    assert "экстремистской организацией" in out
    # второе упоминание Meta не помечается
    assert "Meta уточнила" in out


def test_footnote_idempotent():
    body = "Meta запускает инструмент."
    once = add_restricted_org_footnotes(body)
    twice = add_restricted_org_footnotes(once)
    assert once == twice


def test_no_footnote_without_mentions():
    body = "Google и Яндекс обновили рекламные кабинеты."
    assert add_restricted_org_footnotes(body) == body


def test_metaverse_not_matched():
    body = "Рынок Metaverse растёт, но данных мало."
    assert add_restricted_org_footnotes(body) == body


def test_footnote_lists_all_present_orgs():
    body = "Facebook и Instagram обновили ленту."
    out = add_restricted_org_footnotes(body)
    assert "Facebook\\*" in out
    assert "Instagram\\*" in out
    assert "Facebook и Instagram принадлежат Meta Platforms" in out


# --- обрезка description ---

def test_short_description_untouched():
    text = "Короткое описание в пределах лимита."
    assert _fit_description(text) == text


def test_description_cut_on_sentence_boundary():
    text = ("Meta оспаривает штраф в $1,4 трлн, почти равный капитализации компании. "
            "Дополнительный контекст, который в лимит уже не влезает и должен отсечься.")
    out = _fit_description(text, limit=160)
    assert len(out) <= 160
    assert out.endswith(".")       # закончили предложением
    assert "…" not in out          # без обрыва многоточием


def test_description_word_boundary_when_no_sentence():
    text = "адлкж " * 60  # длинный поток без знаков конца предложения
    out = _fit_description(text, limit=160)
    assert len(out) <= 160
    assert out.endswith("…")
    assert not out.endswith(" …")  # не режем посреди слова с висящим пробелом


def test_description_never_splits_word_midway():
    text = "Компания называет сумму необоснованной " + "и повторяет это раз за разом " * 10
    out = _fit_description(text, limit=160)
    assert len(out) <= 160
    # хвост — целое слово либо законченное предложение, не «необос»
    assert not out.rstrip("…").endswith("необос")


# --- текстовая близость заголовков ---

def test_near_identical_titles_high():
    a = "DuckDuckGo включил блокировку рекламы на YouTube по умолчанию"
    b = "DuckDuckGo по умолчанию блокирует рекламу YouTube"
    assert title_similarity(a, b) >= 0.62


def test_different_meta_stories_low():
    a = "Meta отвергает обвинения в намеренном продвижении рекламы детям"
    b = "Генпрокуроры 29 штатов оценили штраф для Meta в $1,4 трлн"
    assert title_similarity(a, b) < 0.62


def test_empty_titles_zero():
    assert title_similarity("", "что угодно") == 0.0


# --- задача 5: двоеточие в заголовке не должно ронять front-matter ---

def _md(front: str, body: str = "Тело новости.") -> str:
    return f"---\n{front}\n---\n\n{body}"


def test_unquoted_colon_in_title_recovered():
    # DeepSeek поставил двоеточие в незакавыченный title → сырой YAML невалиден
    text = _md('title: Рекламный пилот в Европу: детали запуска\n'
               'description: "Короткое описание"\n'
               'category: "adtech-world"')
    meta, body = split_front_matter(text)
    assert meta["title"] == "Рекламный пилот в Европу: детали запуска"
    assert meta["description"] == "Короткое описание"
    assert body == "Тело новости."


def test_unquoted_colon_in_description_recovered():
    text = _md('title: "Обычный заголовок"\n'
               'description: Итог: рынок вырос вдвое за год')
    meta, _ = split_front_matter(text)
    assert meta["description"] == "Итог: рынок вырос вдвое за год"


def test_valid_front_matter_unchanged_by_repair():
    # штатный (валидный) YAML не должен затрагиваться механизмом починки
    text = _md('title: "Заголовок"\n'
               'geo: ["МИР"]\n'
               'tags: []\n'
               'source:\n  title: "AdExchanger"\n  url: "https://x.com/a"')
    meta, _ = split_front_matter(text)
    assert meta["geo"] == ["МИР"]
    assert meta["tags"] == []
    assert meta["source"] == {"title": "AdExchanger", "url": "https://x.com/a"}


def test_title_with_quotes_and_colon_recovered():
    text = _md('title: Meta заявила: «формат» под вопросом\n'
               'category: "adtech-world"')
    meta, _ = split_front_matter(text)
    assert meta["title"] == "Meta заявила: «формат» под вопросом"


# --- задача 6: авто-ретрай при промахе длины заголовка ---

class _FakeClient:
    """Отдаёт заранее заготовленные ответы по порядку, копит вызовы."""

    def __init__(self, answers):
        self._answers = list(answers)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self._answers.pop(0)


def _writer_answer(title: str) -> str:
    return _md(f'title: "{title}"\n'
               'description: "Описание"\n'
               'pubDate: "AUTO"\n'
               'category: "adtech-world"\n'
               'geo: ["МИР"]\n'
               'tags: []\n'
               'source:\n  title: "AdExchanger"\n  url: "https://adexchanger.com/x"')


def _writer_event():
    primary = {"source_name": "AdExchanger", "source_url": "https://adexchanger.com/x",
               "title_ru": "T", "content_ru": "C", "summary_ru": "S",
               "title_original": "T", "is_primary": True}
    return {"event_id": "e1", "region": "world",
            "published_at": "2026-07-06T10:30:00Z", "sources": [primary]}


def test_title_len_ok_boundaries():
    assert _title_len_ok({"title": "x" * 50})
    assert _title_len_ok({"title": "x" * 80})
    assert not _title_len_ok({"title": "x" * 49})
    assert not _title_len_ok({"title": "x" * 81})


def test_retry_hint_direction():
    assert "удлини" in _retry_hint("x" * 40)
    assert "сократи" in _retry_hint("x" * 90)


def test_write_news_retries_short_title(tmp_path, monkeypatch):
    bad = _writer_answer("Слишком короткий заголовок")            # < 50
    good = _writer_answer("Заголовок нужной длины, укладывается в положенный диапазон")  # 50–80
    fake = _FakeClient([bad, good])
    monkeypatch.setattr(nw, "pipeline_client", lambda stage, state: (fake, "m"))
    monkeypatch.setattr(nw, "DRAFTS_DIR", tmp_path / "news")

    path = nw.write_news(_writer_event(), state=None)
    meta, _ = split_front_matter(path.read_text(encoding="utf-8"))
    assert _title_len_ok(meta)                       # взят исправленный вариант
    assert len(fake.calls) == 2                       # был ровно один ретрай
    assert "ВНИМАНИЕ" in fake.calls[1]["user"]        # хинт добавлен во второй промпт


def test_write_news_no_retry_when_title_ok(tmp_path, monkeypatch):
    good = _writer_answer("Заголовок нужной длины, укладывается в положенный диапазон")
    fake = _FakeClient([good])
    monkeypatch.setattr(nw, "pipeline_client", lambda stage, state: (fake, "m"))
    monkeypatch.setattr(nw, "DRAFTS_DIR", tmp_path / "news")

    nw.write_news(_writer_event(), state=None)
    assert len(fake.calls) == 1                       # ретрая не было


def test_write_news_keeps_first_when_retry_also_bad(tmp_path, monkeypatch):
    bad1 = _writer_answer("Короткий один")
    bad2 = _writer_answer("Короткий два")
    fake = _FakeClient([bad1, bad2])
    monkeypatch.setattr(nw, "pipeline_client", lambda stage, state: (fake, "m"))
    monkeypatch.setattr(nw, "DRAFTS_DIR", tmp_path / "news")

    path = nw.write_news(_writer_event(), state=None)
    meta, _ = split_front_matter(path.read_text(encoding="utf-8"))
    assert meta["title"] == "Короткий один"           # первый вариант, QA разберётся
    assert len(fake.calls) == 2


# --- задача 4: петля переписывания «ИИ-голоса» по замечаниям QA ---

def test_feedback_hint_contains_issues_and_instruction():
    h = nw._feedback_hint("дежурная концовка; клише «меняет правила»")
    assert "дежурная концовка" in h
    assert "ЗАНОВО" in h                               # просим переписать, а не подправить


def test_write_news_injects_feedback_into_prompt(tmp_path, monkeypatch):
    good = _writer_answer("Заголовок нужной длины, укладывается в положенный диапазон")
    fake = _FakeClient([good])
    monkeypatch.setattr(nw, "pipeline_client", lambda stage, state: (fake, "m"))
    monkeypatch.setattr(nw, "DRAFTS_DIR", tmp_path / "news")

    nw.write_news(_writer_event(), state=None, feedback="дежурная концовка")
    assert "дежурная концовка" in fake.calls[0]["user"]  # замечание ушло писателю
    assert "ЗАНОВО" in fake.calls[0]["user"]


def test_write_news_no_feedback_by_default(tmp_path, monkeypatch):
    good = _writer_answer("Заголовок нужной длины, укладывается в положенный диапазон")
    fake = _FakeClient([good])
    monkeypatch.setattr(nw, "pipeline_client", lambda stage, state: (fake, "m"))
    monkeypatch.setattr(nw, "DRAFTS_DIR", tmp_path / "news")

    nw.write_news(_writer_event(), state=None)
    assert "ЗАНОВО" not in fake.calls[0]["user"]        # штатный проход без приписки


def test_qaresult_defaults():
    from src.processors.qa import QAResult

    r = QAResult()
    assert r.llm_issues == []
    assert r.retryable_style is False


# --- задача 4: оркестрация петли в stage_write ---

def _stage_write_env(monkeypatch, tmp_path, qa_results, calls):
    """Патчит write/enrich/qa для теста stage_write; возвращает fake StateManager."""
    import run_pipeline as rp
    from src.processors import qa as qa_mod  # noqa: F401 — держим модуль импортированным

    events = [{"event_id": "e1", "region": "world",
               "sources": [{"is_primary": True, "content_ru": "C"}]}]
    monkeypatch.setattr(rp, "read_jsonl", lambda path: events)

    def fake_write(event, state, feedback=None):
        calls["write"] += 1
        calls["feedbacks"].append(feedback)
        return tmp_path / f"draft-{event['event_id']}.md"

    def fake_enrich(draft, state, article_type="news", region="world"):
        return draft

    def fake_qa(draft, state, source_content="", article_type="news"):
        return qa_results.pop(0)

    monkeypatch.setattr("src.writers.news_writer.write_news", fake_write)
    monkeypatch.setattr("src.processors.enricher.enrich_draft", fake_enrich)
    monkeypatch.setattr("src.processors.qa.run_qa", fake_qa)

    class _State:
        def set_last_run(self, *a):
            pass

    return rp, _State()


def _qa(status, retryable=False, issues=None):
    from src.processors.qa import QAResult

    return QAResult(status=status, retryable_style=retryable, llm_issues=issues or [])


def test_stage_write_recovers_after_style_fail(tmp_path, monkeypatch):
    calls = {"write": 0, "feedbacks": []}
    results = [_qa("FAIL", retryable=True, issues=["дежурная концовка"]), _qa("PASS")]
    rp, state = _stage_write_env(monkeypatch, tmp_path, results, calls)

    metrics = rp.stage_write(state)
    assert metrics == {"qa_passed": 1, "qa_failed": 0, "qa_recovered": 1}
    assert calls["write"] == 2                          # был один проход переписывания
    assert calls["feedbacks"] == [None, "дежурная концовка"]  # замечания ушли во вторую попытку


def test_stage_write_no_retry_on_rules_fail(tmp_path, monkeypatch):
    calls = {"write": 0, "feedbacks": []}
    results = [_qa("FAIL", retryable=False)]            # завернули rules (не стиль) — не переписываем
    rp, state = _stage_write_env(monkeypatch, tmp_path, results, calls)

    metrics = rp.stage_write(state)
    assert metrics == {"qa_passed": 0, "qa_failed": 1, "qa_recovered": 0}
    assert calls["write"] == 1


def test_stage_write_counts_failed_when_rewrite_also_fails(tmp_path, monkeypatch):
    calls = {"write": 0, "feedbacks": []}
    results = [_qa("FAIL", retryable=True, issues=["x"]), _qa("FAIL", retryable=True, issues=["x"])]
    rp, state = _stage_write_env(monkeypatch, tmp_path, results, calls)

    metrics = rp.stage_write(state)
    assert metrics == {"qa_passed": 0, "qa_failed": 1, "qa_recovered": 0}
    assert calls["write"] == 2                          # одна попытка переписывания, дальше не крутим
