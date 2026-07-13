"""Разбор и сборка Markdown-файлов с YAML front-matter."""
from __future__ import annotations

import re

import yaml

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_FENCE_RE = re.compile(r"\A```[a-zA-Z]*\s*\n(.*?)\n```\s*\Z", re.DOTALL)

# Скалярные строковые поля, значение которых модель иногда пишет без кавычек.
# Двоеточие в таком значении (напр. `title: Реклама в Европу: детали`) ломает
# YAML («mapping values are not allowed here») и терял весь материал (задача 5).
_QUOTABLE_FIELDS = {"title", "description", "social_title", "pubDate"}
_KEY_LINE_RE = re.compile(r"^(\s*)([A-Za-z_][\w-]*):[ \t]*(\S.*)$")


def strip_code_fence(text: str) -> str:
    """LLM любит заворачивать ответ в ```markdown ... ``` — снимаем."""
    text = text.strip()
    m = _FENCE_RE.match(text)
    return m.group(1).strip() if m else text


def _quote_scalar(value: str) -> str:
    """Оборачивает значение в двойные кавычки, экранируя внутренние кавычки/слэши."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]  # снимаем уже имеющиеся кавычки, чтобы не задваивать
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _repair_front_matter(raw: str) -> str:
    """Чинит невалидный YAML: квотит «сырые» значения строковых полей.

    Срабатывает только когда обычный разбор упал: у строкового поля значение
    без кавычек с двоеточием внутри. Такие значения оборачиваем в кавычки,
    остальные строки не трогаем.
    """
    fixed = []
    for line in raw.split("\n"):
        m = _KEY_LINE_RE.match(line)
        if m and m.group(2) in _QUOTABLE_FIELDS:
            indent, key, value = m.groups()
            value = value.strip()
            already_quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"
            if not already_quoted:
                fixed.append(f"{indent}{key}: {_quote_scalar(value)}")
                continue
        fixed.append(line)
    return "\n".join(fixed)


def split_front_matter(text: str) -> tuple[dict, str]:
    """Возвращает (front-matter dict, body). ValueError, если front-matter нет."""
    text = strip_code_fence(text)
    m = _FM_RE.match(text)
    if not m:
        raise ValueError("front-matter не найден (нет блока --- ... ---)")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        # Частая причина — двоеточие в незакавыченном заголовке/описании.
        # Пробуем починить квотированием строковых полей и разобрать ещё раз.
        meta = yaml.safe_load(_repair_front_matter(m.group(1))) or {}
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
