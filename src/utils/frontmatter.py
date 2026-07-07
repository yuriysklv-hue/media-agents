"""Разбор и сборка Markdown-файлов с YAML front-matter."""
from __future__ import annotations

import re

import yaml

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_FENCE_RE = re.compile(r"\A```[a-zA-Z]*\s*\n(.*?)\n```\s*\Z", re.DOTALL)


def strip_code_fence(text: str) -> str:
    """LLM любит заворачивать ответ в ```markdown ... ``` — снимаем."""
    text = text.strip()
    m = _FENCE_RE.match(text)
    return m.group(1).strip() if m else text


def split_front_matter(text: str) -> tuple[dict, str]:
    """Возвращает (front-matter dict, body). ValueError, если front-matter нет."""
    text = strip_code_fence(text)
    m = _FM_RE.match(text)
    if not m:
        raise ValueError("front-matter не найден (нет блока --- ... ---)")
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError("front-matter не является YAML-объектом")
    body = text[m.end():].strip()
    return meta, body


# Порядок полей во front-matter — как в контракте с сайтом (ТЗ, раздел 5.5/5.6).
FIELD_ORDER = [
    "title",
    "description",
    "pubDate",
    "author",
    "category",
    "geo",
    "tags",
    "featured",
    "readingTime",
    "highlights",
    "social_title",
    "sources_count",
    "week",
    "source",
]


def render_markdown(meta: dict, body: str) -> str:
    """Собирает .md: front-matter в контрактном порядке полей + тело."""
    ordered = {k: meta[k] for k in FIELD_ORDER if k in meta}
    ordered.update({k: v for k, v in meta.items() if k not in ordered})
    fm = yaml.dump(
        ordered, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000
    )
    return f"---\n{fm}---\n\n{body.strip()}\n"
