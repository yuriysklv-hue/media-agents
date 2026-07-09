"""Юридические сноски: организации, признанные в РФ экстремистскими/запрещёнными.

Российские медиа обязаны при упоминании таких организаций давать пометку.
Проставляется детерминированно после написания текста, чтобы не зависеть от
дисциплины модели: помечается ПЕРВОЕ упоминание каждого термина, сноска — одна,
в конце тела. Звёздочки экранируются (\\*), чтобы не сломать Markdown-разметку
(жирный **текст**, курсив).
"""
from __future__ import annotations

import re

# Термины, требующие сноски. WhatsApp сознательно НЕ включён: принадлежит Meta,
# но на территории РФ не запрещён.
RESTRICTED_ORGS = ("Meta", "Facebook", "Instagram")
MARKER = "\\*"  # экранированная звёздочка — литеральный «*» в Markdown


def _footnote_text(names: list[str]) -> str:
    if names == ["Meta"]:
        return ("Meta Platforms признана в России экстремистской организацией, "
                "её деятельность на территории РФ запрещена.")
    if len(names) == 1:  # только Facebook или только Instagram
        return (f"{names[0]} принадлежит Meta Platforms, которая признана в России "
                "экстремистской организацией; её деятельность в РФ запрещена.")
    joined = ", ".join(names[:-1]) + f" и {names[-1]}"
    return (f"{joined} принадлежат Meta Platforms, которая признана в России "
            "экстремистской организацией; её деятельность в РФ запрещена.")


def add_restricted_org_footnotes(body: str) -> str:
    """Помечает первое упоминание каждой запрещённой организации и добавляет сноску.

    Идемпотентна: если сноска уже стоит (в тексте есть маркер `\\*`), тело не
    трогается.
    """
    if MARKER in body:
        return body
    present: list[str] = []
    result = body
    for org in RESTRICTED_ORGS:
        # Латинское слово целиком: не цепляем Metaverse и уже помеченное.
        pattern = re.compile(rf"(?<![\w\\*])({re.escape(org)})(?![\w*])")
        m = pattern.search(result)
        if not m:
            continue
        present.append(org)
        result = result[: m.end()] + MARKER + result[m.end():]
    if not present:
        return body
    return result.rstrip() + f"\n\n{MARKER} {_footnote_text(present)}"
