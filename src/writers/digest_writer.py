"""Author Digest: недельный дайджест по опубликованным новостям со ссылками."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..llm_client import pipeline_client
from ..utils.config import DATA_DIR, fill_prompt, load_prompt
from ..utils.frontmatter import render_markdown, split_front_matter
from ..utils.logger import get_logger
from ..utils.state import StateManager

log = get_logger("digest_writer")

DRAFTS_DIR = DATA_DIR / "drafts" / "digest"


def iso_week(day: date | None = None) -> str:
    day = day or datetime.now(timezone.utc).date()
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def week_bounds(week: str) -> tuple[date, date]:
    """'2026-W28' → (понедельник, воскресенье)."""
    year, w = week.split("-W")
    monday = date.fromisocalendar(int(year), int(w), 1)
    return monday, monday + timedelta(days=6)


def _news_for_week_from_site(week: str) -> list[dict]:
    """Новости недели из ЖИВОЙ коллекции сайта (репо media), а не из published.jsonl.

    Источник истины — то, что реально смёржено и лежит на сайте
    (`media-site/src/content/news/*.md`): `published.jsonl` = «отправлено в PR», но
    часть материалов владелец удаляет из PR перед мержем, а `pr_merged` не
    отслеживается → дайджест по нему дал бы битые ссылки `/article/{slug}`. Здесь
    slug = имя файла = роут сайта, поэтому ссылки гарантированно валидны.

    Клон не поднялся → исключение пробрасывается наверх (дайджест не выпускаем).
    Папки коллекции нет / нет статей за неделю → пустой список (дайджест не пишем).
    """
    from ..publishers.media_repo import clone_or_update_media, news_dir

    ndir = news_dir(clone_or_update_media())
    if not ndir.exists():
        return []

    start, end = week_bounds(week)
    lo, hi = start.isoformat(), end.isoformat()
    items: list[dict] = []
    for md in sorted(ndir.glob("*.md")):
        meta, _ = split_front_matter(md.read_text(encoding="utf-8"))
        pub = str(meta.get("pubDate", ""))[:10]  # pubDate — ISO с временем UTC, берём date-часть
        if not (lo <= pub <= hi):
            continue
        source = meta.get("source") or {}
        items.append({
            "slug": md.stem,  # slug = имя файла = роут /article/<slug>
            "title": meta.get("title", ""),
            "category": meta.get("category", ""),
            "geo": meta.get("geo", []),
            "source_name": source.get("title", ""),
            "source_url": source.get("url", ""),
            "pub_date": str(meta.get("pubDate", "")),
        })
    return sorted(items, key=lambda r: r.get("pub_date", ""))


def _news_list_block(items: list[dict]) -> str:
    lines = []
    for n, rec in enumerate(items, 1):
        lines += [
            f"{n}. Title: \"{rec['title']}\"",
            f"   Slug: {rec['slug']}",
            f"   Category: {rec.get('category', '')}",
            f"   Source: {rec.get('source_name', '')}",
            f"   PubDate: {str(rec.get('pub_date', ''))[:10]}",
            "",
        ]
    return "\n".join(lines)


def write_digest(state: StateManager, week: str | None = None) -> Path | None:
    """Пишет черновик data/drafts/digest/draft-{week}.md. None — новостей нет."""
    week = week or iso_week()
    items = _news_for_week_from_site(week)
    if not items:
        log.warning("за неделю %s нет живущих на сайте новостей — дайджест не пишем", week)
        return None

    start, end = week_bounds(week)
    client, model = pipeline_client("digest_writer", state)
    prompt = fill_prompt(
        load_prompt("digest_writer"),
        week_range=f"{start.isoformat()} — {end.isoformat()}",
        news_list_with_slugs=_news_list_block(items),
        pub_date=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    answer = client.chat(
        model=model, system="", user=prompt,
        temperature=0.7, max_tokens=8192,
        stage="digest_writer", item_id=week,
    )

    meta, body = split_front_matter(answer)
    meta["week"] = week
    meta["sources_count"] = len(items)
    meta.setdefault("category", "adtech-world")
    meta.setdefault("geo", ["МИР"])
    meta.setdefault("tags", ["weekly-digest"])

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    path = DRAFTS_DIR / f"draft-{week.lower()}.md"
    path.write_text(render_markdown(meta, body), encoding="utf-8")
    log.info("дайджест %s написан: %d новостей, %d знаков", week, len(items), len(body))
    return path
