"""Тесты TF-IDF дедупа: движок, кластеризация в батче, отсев дублей, ArticleStore."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.processors import filter_dedup as fd
from src.utils.store import ArticleStore
from src.utils.tfidf_engine import cross_similarity, pairwise_similarity


# --- движок ---

def test_pairwise_identical_high_different_low():
    texts = [
        "Google запускает новый рекламный формат в поиске",
        "Google запустил новый формат рекламы в поисковой выдаче",
        "TikTok сокращает штат модераторов в Европе",
    ]
    sim = pairwise_similarity(texts)
    assert sim.shape == (3, 3)
    # перефразировка одного сюжета ближе, чем разные сюжеты
    assert sim[0][1] > sim[0][2]
    assert sim[0][1] > 0.4
    assert sim[0][2] < sim[0][1]


def test_cross_similarity_shapes_and_empty():
    assert cross_similarity([], ["a"]).shape == (0, 1)
    assert cross_similarity(["a"], []).shape == (1, 0)
    m = cross_similarity(["Meta меняет таргетинг рекламы"],
                         ["Meta обновляет таргетинг в рекламе", "Погода в Москве"])
    assert m.shape == (1, 2)
    assert m[0][0] > m[0][1]  # адтех-заголовок ближе к адтех-корпусу


def test_char_ngrams_handle_russian_morphology():
    # словоизменение: движок должен видеть близость несмотря на окончания
    sim = pairwise_similarity(["Роскомнадзор блокирует сервис",
                               "Роскомнадзор заблокировал сервисы"])
    assert sim[0][1] > 0.4


# --- кластеризация в filter_dedup ---

def _item(id_, title, summary=""):
    return {"id": id_, "title_ru": title, "summary_ru": summary,
            "title": title, "source_name": f"src-{id_}",
            "source_url": f"https://ex.com/{id_}", "title_original": title,
            "content_ru": summary or title, "llm_score": 7}


class _NoPublished:
    """Заглушка state: пустой корпус опубликованного (без LLM)."""
    def load_published(self):
        return []


def test_two_similar_items_one_cluster():
    items = [
        _item("a", "Google запускает новый формат рекламы в поиске",
              "Компания Google представила формат объявлений в поисковой выдаче"),
        _item("b", "Google представил новый рекламный формат в поисковой выдаче",
              "Новый формат рекламы Google в поиске уже доступен рекламодателям"),
        _item("c", "TikTok сокращает команду модерации в Европе",
              "ByteDance урезает штат модераторов TikTok в европейских офисах"),
    ]
    result = fd.DedupResult()
    events = fd._tfidf_events(items, _NoPublished(), result)
    # a+b склеились в одно событие, c — отдельно → 2 события
    assert len(events) == 2
    multi = [e for e in events if e["source_count"] > 1]
    assert len(multi) == 1
    assert multi[0]["source_count"] == 2


def test_distinct_items_not_merged():
    items = [
        _item("a", "Яндекс обновил рекламный кабинет для малого бизнеса"),
        _item("b", "Amazon купила стартап по измерению эффективности рекламы"),
    ]
    result = fd.DedupResult()
    events = fd._tfidf_events(items, _NoPublished(), result)
    assert len(events) == 2


def test_dup_of_published_dropped():
    class _Pub:
        def load_published(self):
            return [{"title": "Meta обновляет таргетинг рекламных кампаний"}]

    items = [
        _item("a", "Meta обновила таргетинг в рекламных кампаниях"),  # дубль
        _item("b", "Reddit запускает биржу видеорекламы"),           # свежий
    ]
    result = fd.DedupResult()
    # порог дубля берём мягче для детерминизма теста
    events = fd._tfidf_events(items, _Pub(), result)
    titles = [e["sources"][0]["title_ru"] for e in events]
    assert result.duplicates >= 1
    assert any("Reddit" in t for t in titles)
    assert all("Meta" not in t for t in titles)


# --- ArticleStore ---

def test_article_store_delegates_and_persists_failed(tmp_path):
    store = ArticleStore(data_dir=tmp_path)
    # делегирование к StateManager
    assert store.load_published() == []
    store.load_seen_urls()  # не падает — метод StateManager
    # своя персистентность брака
    store.add_failed_draft({"slug": "x", "title": "T", "reasons": ["ИИ-голос"]})
    failed = store.load_failed_drafts()
    assert len(failed) == 1
    assert failed[0]["slug"] == "x"
    assert failed[0]["logged_at"]  # проставлен автоматически
    assert store.failed_drafts_path.exists()
