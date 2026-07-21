"""Юридические сноски: организации, признанные в РФ экстремистскими/запрещёнными.

Российские медиа обязаны при упоминании таких организаций давать пометку.
Проставляется детерминированно после написания текста, чтобы не зависеть от
дисциплины модели: помечается КАЖДОЕ упоминание каждого термина (не только
первое — иначе часть «Meta» в теле остаётся без связи со сноской, чего допускать
нельзя), сноска — одна, в конце тела. Звёздочки экранируются (\\*), чтобы не
сломать Markdown-разметку (жирный **текст**, курсив).
"""
from __future__ import annotations

import re

# Термины, требующие сноски. WhatsApp сознательно НЕ включён: принадлежит Meta,
# но на территории РФ не запрещён.
RESTRICTED_ORGS = ("Meta", "Facebook", "Instagram")
MARKER = "\\*"  # экранированная звёздочка — литеральный «*» в Markdown
# Сигнатура уже проставленной сноски — по ней ловим идемпотентность: если сноска
# в теле есть, повторный вызов ничего не трогает (и не метит «Meta» внутри самой
# сноски). На первом проходе тело сноски добавляется ПОСЛЕ маркировки, поэтому в
# нём «Meta Platforms» остаётся без маркера.
_FOOTNOTE_SIGNATURE = "признана в России экстремистской организацией"


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
    """Помечает КАЖДОЕ упоминание каждой запрещённой организации и добавляет сноску.

    Идемпотентна: если сноска уже стоит (сигнатура в теле), тело не трогается.
    """
    if _FOOTNOTE_SIGNATURE in body:
        return body
    present: list[str] = []
    result = body
    for org in RESTRICTED_ORGS:
        # Латинское слово целиком: не цепляем Metaverse (lookahead на \w). Идём
        # слева направо, продолжая поиск после вставленного маркера, чтобы
        # пометить ВСЕ вхождения, а не только первое.
        pattern = re.compile(rf"(?<![\w\\*])({re.escape(org)})(?![\w*])")
        found = False
        pos = 0
        while True:
            m = pattern.search(result, pos)
            if not m:
                break
            found = True
            insert_at = m.end()
            result = result[:insert_at] + MARKER + result[insert_at:]
            pos = insert_at + len(MARKER)
        if found:
            present.append(org)
    if not present:
        return body
    return result.rstrip() + f"\n\n{MARKER} {_footnote_text(present)}"
