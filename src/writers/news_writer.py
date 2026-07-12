"""Author News: оригинальная новость на русском по переведённым источникам.

Не перевод и не рерайт перевода — самостоятельный журналистский текст.
Перевод передаётся автору как рабочий материал для извлечения фактов.
"""
from __future__ import annotations

from pathlib import Path

from ..llm_client import pipeline_client
from ..utils.config import DATA_DIR, fill_prompt, load_prompt
from ..utils.frontmatter import render_markdown, split_front_matter
from ..utils.legal import add_restricted_org_footnotes
from ..utils.logger import get_logger
from ..utils.state import StateManager, utcnow_iso

log = get_logger("news_writer")

DRAFTS_DIR = DATA_DIR / "drafts" / "news"


def _sources_block(event: dict) -> str:
    sources = event["sources"]
    if len(sources) == 1:
        s = sources[0]
        return (
            "Источник (переведённый рабочий материал):\n"
            f"- Заголовок: {s['title_ru']}\n"
            f"- Текст: {s['content_ru'] or s['summary_ru']}\n"
            f"- Источник: {s['source_name']} ({s['source_url']})\n"
            f"- Оригинальный заголовок (для сверки фактов): {s['title_original']}"
        )
    lines = ["Источники (переведённый рабочий материал — синтезируй факты из всех):", ""]
    for n, s in enumerate(sources, 1):
        primary = " (primary)" if s.get("is_primary") else ""
        lines += [
            f"[{n}] {s['source_name']}{primary}",
            f"- Заголовок: {s['title_ru']}",
            f"- Текст: {s['content_ru'] or s['summary_ru']}",
            f"- Оригинальный заголовок: {s['title_original']}",
            f"- URL: {s['source_url']}",
            "",
        ]
    lines.append(
        "Ссылайся на primary source в front-matter. В тексте можешь ссылаться "
        "на дополнительные источники, если они добавляют факты."
    )
    return "\n".join(lines)


def _finalize_meta(meta: dict, event: dict, primary: dict) -> dict:
    """Детерминированная доводка front-matter после генерации модели.

    Источник — из сырья, а не из фантазии модели. `pubDate` — момент выхода
    материала на 1screen (UTC now), а НЕ дата публикации в источнике из RSS
    (`event["published_at"]`): иначе статьи датируются задним числом, выглядят
    несвежими, не зажигают live-точку на главной (isLive = моложе 3 ч) и тонут
    внизу ленты (сортировка по pubDate убыв.). Дата источника не теряется —
    паблишер сохраняет её в published.jsonl (`source_published_at`); во
    front-matter она не идёт, чтобы не расширять контракт схемы сайта.
    """
    meta["source"] = {"title": primary["source_name"], "url": primary["source_url"]}
    meta["pubDate"] = utcnow_iso()
    # Сид категории/geo по региону источника; Enricher уточнит и проставит автора.
    region = event.get("region", "world")
    meta.setdefault("category", "adtech-ru" if region == "ru" else "adtech-world")
    meta.setdefault("geo", ["РФ"] if region == "ru" else ["МИР"])
    return meta


def write_news(event: dict, state: StateManager) -> Path:
    """Пишет черновик data/drafts/news/draft-{event_id}.md (slug проставит Enricher)."""
    client, model = pipeline_client("news_writer", state)
    primary = next(s for s in event["sources"] if s.get("is_primary"))

    prompt = fill_prompt(
        load_prompt("news_writer"),
        sources_block=_sources_block(event),
        primary_source_name=primary["source_name"],
        primary_source_url=primary["source_url"],
    )
    answer = client.chat(
        model=model, system="", user=prompt,
        temperature=0.7, max_tokens=4096,
        stage="news_writer", item_id=event["event_id"],
    )

    meta, body = split_front_matter(answer)  # ValueError → событие в drafts/failed решает вызывающий
    body = add_restricted_org_footnotes(body)  # сноска о запрещённых в РФ организациях
    _finalize_meta(meta, event, primary)

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    path = DRAFTS_DIR / f"draft-{event['event_id']}.md"
    path.write_text(render_markdown(meta, body), encoding="utf-8")
    log.info("черновик написан: %s (%d знаков)", path.name, len(body))
    return path
