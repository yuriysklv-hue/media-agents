"""Enricher: slug, description, category/geo, tags, author, social_title, readingTime.

Модель GLM-4-Flash; при её недоступности — детерминированный фолбэк
(slug транслитом, description из тела), пайплайн продолжает работу.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..llm_client import LLMUnavailable, parse_json_response, pipeline_client
from ..utils.config import fill_prompt, load_config, load_prompt
from ..utils.frontmatter import render_markdown, split_front_matter
from ..utils.logger import get_logger
from ..utils.slug import ensure_unique, generate_slug
from ..utils.state import StateManager

log = get_logger("enricher")

READING_WPM = 180  # слов в минуту для расчёта readingTime


def _reading_time(body: str) -> int:
    return max(1, round(len(body.split()) / READING_WPM))


_SENT_END = re.compile(r"[.!?…](?:\s|$)")


def _fit_description(text: str, limit: int = 160) -> str:
    """Укладывает описание в limit символов ЗАКОНЧЕННОЙ фразой.

    Дескрипшен показывается как лид-абзац материала, поэтому обрыв посреди слова
    («…необос…») недопустим. Сначала пробуем границу предложения в пределах
    лимита; если содержательного куска нет — режем по границе слова и ставим «…»
    (это уже честное усечение, а не обрыв середины слова).
    """
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    window = text[:limit]
    last_sentence = 0
    for m in _SENT_END.finditer(window):
        last_sentence = m.start() + 1  # включаем сам знак препинания
    if last_sentence >= 80:  # кусок достаточно содержательный — обходимся без «…»
        return window[:last_sentence].rstrip()
    cut = window[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:—–-")
    return (cut or window[: limit - 1].rstrip()) + "…"


def _existing_slugs(state: StateManager, drafts_dir: Path) -> set[str]:
    slugs = {rec["slug"] for rec in state.load_published() if rec.get("slug")}
    slugs |= {p.stem for p in drafts_dir.glob("*.md")}
    return slugs


def _llm_enrich(meta: dict, body: str, state: StateManager) -> dict:
    vocab = load_config("vocabulary")
    client, model = pipeline_client("enricher", state)
    prompt = fill_prompt(
        load_prompt("enricher"),
        categories_list=", ".join(vocab["categories"]),
        geo_values=", ".join(vocab["geo_values"]),
        tags_list=", ".join(vocab["tags"]),
        title=meta.get("title", ""),
        body_preview=body[:500],
        current_frontmatter=json.dumps(
            {k: meta.get(k) for k in ("description", "category", "geo", "tags")},
            ensure_ascii=False,
        ),
    )
    answer = client.chat(
        model=model, system="", user=prompt,
        temperature=0.2, max_tokens=800,
        response_format={"type": "json_object"}, stage="enricher",
    )
    return parse_json_response(answer)


def _validate_tags(tags: list, vocab: dict) -> list[str]:
    """Теги из словаря пропускаем как есть; новые — только с пометкой [new]."""
    known = {t.lower() for t in vocab["tags"]}
    out = []
    for tag in tags or []:
        tag = str(tag).strip()
        if not tag:
            continue
        if tag.lower() in known or tag.endswith("[new]"):
            out.append(tag)
    return out[:7]


def enrich_draft(path: Path, state: StateManager, article_type: str = "news") -> Path:
    """Дополняет front-matter черновика и переименовывает файл в {slug}.md."""
    vocab = load_config("vocabulary")
    authors = load_config("authors")
    meta, body = split_front_matter(path.read_text(encoding="utf-8"))

    enriched: dict = {}
    try:
        enriched = _llm_enrich(meta, body, state) or {}
    except (LLMUnavailable, Exception) as exc:  # noqa: BLE001 — фолбэк осознанный
        log.warning("enricher-LLM не отработал (%s) — детерминированный фолбэк", exc)

    # Slug: LLM-вариант, иначе транслит заголовка. Уникальность — обязательна.
    raw_slug = str(enriched.get("slug") or "").strip() or generate_slug(meta.get("title", ""))
    raw_slug = generate_slug(raw_slug)  # нормализация LLM-варианта
    slug = ensure_unique(raw_slug, _existing_slugs(state, path.parent) - {path.stem})

    description = str(enriched.get("description") or meta.get("description") or "").strip()
    if not description:
        description = " ".join(body.split())  # фолбэк: начало тела одной строкой
    meta["description"] = _fit_description(description)

    category = str(enriched.get("category") or meta.get("category") or "adtech-world")
    if category not in vocab["categories"]:
        log.warning("категория «%s» вне словаря — заменяю на adtech-world", category)
        category = "adtech-world"
    meta["category"] = category

    geo = enriched.get("geo") or meta.get("geo") or ["МИР"]
    meta["geo"] = [g for g in geo if g in vocab["geo_values"]] or ["МИР"]

    tags = _validate_tags(enriched.get("tags") or meta.get("tags") or [], vocab)
    if tags:
        meta["tags"] = tags

    social_title = str(enriched.get("social_title") or "").strip()
    if social_title:
        meta["social_title"] = social_title[:100]

    meta["author"] = authors["category_author_map"].get(
        category, authors.get("default_author", "news-world")
    )
    meta.setdefault("featured", False)
    meta["readingTime"] = _reading_time(body)
    if article_type == "news":
        meta.setdefault("highlights", [])

    new_path = path.with_name(f"{slug}.md")
    new_path.write_text(render_markdown(meta, body), encoding="utf-8")
    if new_path != path:
        path.unlink()
    log.info("enriched: %s (category=%s, author=%s)", new_path.name, category, meta["author"])
    return new_path
