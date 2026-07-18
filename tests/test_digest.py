"""Тесты дайджеста на материалах сайта (задача №3).

Источник новостей — живая коллекция репо media (`.md`), не published.jsonl.
Клон мокается на tmp-папку с подготовленными файлами — без сети.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.processors import enricher
from src.publishers import media_repo
from src.utils.frontmatter import render_markdown
from src.writers import digest_writer
from src.writers.digest_writer import (
    _news_for_week_from_site,
    _news_list_block,
    iso_week,
    week_bounds,
    write_digest,
)


# --- границы недели ---

def test_iso_week_and_bounds():
    # 2026-07-15 — среда 29-й ISO-недели
    week = iso_week(date(2026, 7, 15))
    assert week == "2026-W29"
    monday, sunday = week_bounds(week)
    assert monday == date(2026, 7, 13)   # понедельник
    assert sunday == date(2026, 7, 19)   # воскресенье (включительно)


# --- выборка из фейковой коллекции сайта ---

def _write_md(path: Path, *, title, pub, category="adtech-world",
              geo=("МИР",), source_title="AdExchanger", source_url="https://x/y"):
    meta = {
        "title": title,
        "pubDate": pub,
        "category": category,
        "geo": list(geo),
        "source": {"title": source_title, "url": source_url},
    }
    path.write_text(render_markdown(meta, "Тело статьи."), encoding="utf-8")


def _make_site(tmp_path: Path) -> Path:
    news = tmp_path / "media-site" / "src" / "content" / "news"
    news.mkdir(parents=True)
    return news


def test_selection_by_pubdate_and_slug(tmp_path, monkeypatch):
    news = _make_site(tmp_path)
    _write_md(news / "in-week-mon.md", title="В неделе (пн)", pub="2026-07-13T09:00:00Z")
    _write_md(news / "in-week-sun.md", title="В неделе (вс)", pub="2026-07-19T20:00:00Z")
    _write_md(news / "before-week.md", title="До недели", pub="2026-07-12T23:59:00Z")
    _write_md(news / "after-week.md", title="После недели", pub="2026-07-20T00:00:00Z")
    monkeypatch.setattr(media_repo, "clone_or_update_media", lambda: tmp_path)

    items = _news_for_week_from_site("2026-W29")

    slugs = [it["slug"] for it in items]
    # только материалы внутри пн–вс включительно; slug = имя файла
    assert slugs == ["in-week-mon", "in-week-sun"]  # + сортировка по pub_date
    assert items[0]["title"] == "В неделе (пн)"
    assert items[0]["source_name"] == "AdExchanger"
    assert items[0]["source_url"] == "https://x/y"


def test_ru_category_preserved(tmp_path, monkeypatch):
    news = _make_site(tmp_path)
    _write_md(news / "ru-news.md", title="Российская новость",
              pub="2026-07-15T09:00:00Z", category="adtech-ru", geo=("РФ",),
              source_title="Яндекс Реклама")
    monkeypatch.setattr(media_repo, "clone_or_update_media", lambda: tmp_path)

    items = _news_for_week_from_site("2026-W29")
    assert len(items) == 1
    # RU-подсекция дайджеста ветвится по category из front-matter — должна долетать
    assert items[0]["category"] == "adtech-ru"
    assert items[0]["geo"] == ["РФ"]


def test_digest_dir_ignored(tmp_path, monkeypatch):
    # дайджесты живут в src/content/digest — не должны попадать в выборку новостей
    news = _make_site(tmp_path)
    _write_md(news / "real-news.md", title="Новость", pub="2026-07-15T09:00:00Z")
    digest_col = tmp_path / "media-site" / "src" / "content" / "digest"
    digest_col.mkdir(parents=True)
    _write_md(digest_col / "draft-2026-w28.md", title="Прошлый дайджест",
              pub="2026-07-15T09:00:00Z")
    monkeypatch.setattr(media_repo, "clone_or_update_media", lambda: tmp_path)

    items = _news_for_week_from_site("2026-W29")
    assert [it["slug"] for it in items] == ["real-news"]


def test_empty_week_returns_none(monkeypatch):
    # пустая неделя → write_digest не пишет и не зовёт LLM (state не нужен)
    monkeypatch.setattr(digest_writer, "_news_for_week_from_site", lambda week: [])
    assert write_digest(state=None, week="2026-W29") is None


def test_missing_news_dir_is_empty(tmp_path, monkeypatch):
    # папки коллекции нет (новый/пустой репо) → пустой список, не падаем
    monkeypatch.setattr(media_repo, "clone_or_update_media", lambda: tmp_path)
    assert _news_for_week_from_site("2026-W29") == []


# --- enricher: slug дайджеста = ISO-неделя, не транслит заголовка ---

class _FakeState:
    state_dir = Path("/tmp")
    def load_published(self):
        return []


def test_digest_slug_is_week(tmp_path, monkeypatch):
    # LLM-обогащение мокаем (без сети): enricher уйдёт в детерминированный путь
    monkeypatch.setattr(enricher, "_llm_enrich", lambda *a, **k: {})
    draft = tmp_path / "draft-2026-w29.md"
    meta = {
        "title": "PayPal доказывает ROI retail media, а атрибуция уходит в кризис",
        "description": "Дайджест недели.",
        "pubDate": "2026-07-18T18:00:00Z",
        "category": "adtech-world",
        "geo": ["МИР"],
        "tags": ["weekly-digest"],
        "week": "2026-W29",
    }
    draft.write_text(render_markdown(meta, "Тело дайджеста. " * 200), encoding="utf-8")

    out = enricher.enrich_draft(draft, _FakeState(), article_type="digest")
    # slug = неделя (эталон media: 2026-w27.md), а НЕ paypal-...
    assert out.name == "2026-w29.md"


# --- блок списка новостей для промпта ---

def test_news_list_block_structure():
    items = [{
        "title": "Заголовок", "slug": "my-slug", "category": "adtech-world",
        "source_name": "Digiday", "pub_date": "2026-07-15T09:00:00Z",
    }]
    block = _news_list_block(items)
    assert '1. Title: "Заголовок"' in block
    assert "Slug: my-slug" in block
    assert "Category: adtech-world" in block
    assert "Source: Digiday" in block
    assert "PubDate: 2026-07-15" in block  # только date-часть
