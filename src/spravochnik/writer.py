"""Writer: справочный материал по типу из research_bundle (DeepSeek-V3).

Не перевод и не рерайт — самостоятельный энциклопедический текст. После генерации
детерминированная доводка (finalize-гейт): сноска РКН, description≤160, pubDate,
slug из очереди, readingTime. JSON-LD Writer НЕ пишет — его собирает сайт из facts.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..llm_client import pipeline_client
from ..processors.enricher import _fit_description
from ..utils.config import DATA_DIR, fill_prompt, load_prompt
from ..utils.frontmatter import render_markdown, split_front_matter
from ..utils.legal import add_restricted_org_footnotes
from ..utils.logger import get_logger
from ..utils.slug import ensure_unique, generate_slug
from ..utils.state import StateManager, utcnow_iso

log = get_logger("spravochnik.writer")

DRAFTS_DIR = DATA_DIR / "spravochnik" / "drafts"

# Шаблоны тела и обязательных полей facts по типам (раздел 2 ТЗ).
BODY_TEMPLATES = {
    "company": "## О компании\n## История\n## Роль в рекламной индустрии\n## Ключевые продукты",
    "technology": "## Что это\n## Как работает\n## Для кого\n## Альтернативы",
    "term": "## Определение\n## Контекст\n## Примеры",
    "organization": "## Что это\n## Ключевые инициативы\n## Значимость",
}
FACTS_TEMPLATES = {
    "company": "founded (год), founders (список), hq, official_url, "
               "subtype, parent_organization?, ticker?, key_products (список)?",
    "technology": "developer, category, official_url, launch_date?, "
                  "pricing_model?, alternatives (список)?",
    "term": "category, definition, aliases (список)?",
    "organization": "full_name, founded (год), mission, official_url, "
                    "key_initiatives (список)?",
}


def _feedback_section(feedback: str | None, iteration: int) -> str:
    if not feedback:
        return ""
    return (
        f"\n\nЭТО ИТЕРАЦИЯ {iteration}. Материал был возвращён с замечаниями:\n"
        f"{feedback}\nУчти эти замечания при переработке, остальное сохрани по существу."
    )


# Маркер неподтверждённого факта из промпта писателя (spravochnik_writer.md):
# {{fact_check: "..."}}. Кавычки любые (модель копирует пример с «ёлочками»),
# регистр/пробелы/разделитель вольные. Non-greedy до первого закрывающего }}.
_FACTCHECK_RE = re.compile(r"\{\{\s*fact[_\s-]?check\b[^}]*\}\}", re.IGNORECASE)
_SENT_END = ".!?…"
_SENT_CLOSERS = "»\"')”"


def _sentence_span(text: str, m_start: int, m_end: int) -> tuple[int, int]:
    """Границы предложения, несущего маркер [m_start:m_end], в пределах строки.

    Назад — до конца прошлого предложения или начала строки; вперёд — до
    терминатора (включая его и закрывающую кавычку/скобку). Не пересекаем \\n,
    чтобы не задеть соседние заголовки/пункты списка.
    """
    i = m_start
    while i > 0 and text[i - 1] not in _SENT_END and text[i - 1] != "\n":
        i -= 1
    j, n = m_end, len(text)
    while j < n and text[j] not in _SENT_END and text[j] != "\n":
        j += 1
    while j < n and text[j] in _SENT_END:
        j += 1
    while j < n and text[j] in _SENT_CLOSERS:
        j += 1
    return i, j


def _strip_unconfirmed_sentences(body: str) -> str:
    """Вырезает предложения с маркером {{fact_check: …}} целиком.

    Маркер — сигнал писателя «факта нет в research, не подтверждено». Публиковать
    нельзя ни маркер, ни само недостоверное утверждение, поэтому убираем всё
    несущее маркер предложение (а не только маркер). Идём справа налево, чтобы
    индексы не съезжали. После — чистим осиротевшие пробелы и пустые абзацы.
    Если тело просело ниже минимума — это поймает rules-QA (→ needs_fix).
    """
    if not _FACTCHECK_RE.search(body):
        return body
    for m in reversed(list(_FACTCHECK_RE.finditer(body))):
        start, end = _sentence_span(body, m.start(), m.end())
        body = body[:start] + body[end:]
    body = re.sub(r"[ \t]+([.,;:!?…])", r"\1", body)  # пробел перед пунктуацией
    body = re.sub(r"[ \t]{2,}", " ", body)            # сдвоенные пробелы
    body = re.sub(r"[ \t]+\n", "\n", body)            # хвостовые пробелы
    body = re.sub(r"\n[ \t]+", "\n", body)            # пробелы в начале строки (шаблоны плоские)
    body = re.sub(r"\n{3,}", "\n\n", body)            # опустевшие абзацы
    return body.strip() + "\n"


def _strip_factcheck_value(value):
    """Рекурсивно убирает маркер {{fact_check: …}} из значения facts (строка/список/словарь).

    Маркер — сигнал писателя для ТЕЛА (там несущее предложение вырезается целиком
    в _strip_unconfirmed_sentences). Если модель вписала его в структурное поле
    facts, вырезать предложение нельзя — вырезаем сам маркер. Возвращает None, если
    строка после вычистки опустела (вызывающий выкинет поле/элемент).
    """
    if isinstance(value, str):
        cleaned = _FACTCHECK_RE.sub("", value).strip(" \t;,.–—-")
        return cleaned or None
    if isinstance(value, list):
        out = []
        for v in value:
            nv = _strip_factcheck_value(v)
            if nv is not None:
                out.append(nv)
        return out
    if isinstance(value, dict):
        return _strip_factcheck_from_facts(value)
    return value


def _strip_factcheck_from_facts(facts: dict) -> dict:
    """Вычищает утёкший маркер {{fact_check: …}} из значений facts.

    Опустевшее после вычистки поле удаляется целиком: лучше отсутствующий факт, чем
    маркер или голая недостоверная строка в опубликованном материале. Если пустым
    осталось ОБЯЗАТЕЛЬНОЕ поле (по типу) — это поймает rules-QA (→ FAIL), что верно:
    материал без обязательного факта публиковать нельзя.
    """
    if not isinstance(facts, dict):
        return facts
    cleaned: dict = {}
    for key, value in facts.items():
        new_value = _strip_factcheck_value(value)
        if new_value is None or (isinstance(new_value, str) and not new_value.strip()):
            continue
        cleaned[key] = new_value
    return cleaned


def _clean_tags(tags) -> list[str]:
    """Дедуп + очистка тегов, кап 7. Таксономия справочника шире новостной —
    жёсткую валидацию против vocabulary не применяем (rules-QA только предупреждает)."""
    seen, out = set(), []
    for tag in tags or []:
        t = str(tag).strip()
        key = t.lower()
        if t and key not in seen:
            seen.add(key)
            out.append(t)
    return out[:7]


def _finalize_meta(meta: dict, item: dict) -> dict:
    """Детерминированная доводка front-matter после генерации модели."""
    meta["type"] = item["type"]
    meta["slug"] = item["slug"]
    meta["title"] = str(meta.get("title") or item["term"]).strip()
    description = str(meta.get("description") or "").strip()
    if description:
        meta["description"] = _fit_description(description)
    meta["tags"] = _clean_tags(meta.get("tags"))
    meta.setdefault("related", [])
    meta["pubDate"] = utcnow_iso()  # момент выхода на 1screen (как у новостей)
    # Маркер {{fact_check}} мог утечь в структурные поля facts — там его нельзя
    # вырезать вместе с предложением (как в теле), поэтому чистим отдельно.
    meta["facts"] = _strip_factcheck_from_facts(meta.get("facts") or {})
    return meta


def write_material(item: dict, research: dict, state: StateManager) -> Path:
    """Пишет черновик data/spravochnik/drafts/{slug}.md.

    item — айтем очереди (term/type/slug/feedback/iteration). research — bundle.
    """
    client, model = pipeline_client("spravochnik_writer", state)
    prompt = fill_prompt(
        load_prompt("spravochnik_writer"),
        type=item["type"],
        term=item["term"],
        slug=item["slug"],
        body_template=BODY_TEMPLATES.get(item["type"], ""),
        facts_template=FACTS_TEMPLATES.get(item["type"], ""),
        research_json=json.dumps(research, ensure_ascii=False, indent=2),
        feedback_section=_feedback_section(item.get("feedback"), item.get("iteration", 1)),
    )
    answer = client.chat(
        model=model, system="", user=prompt,
        temperature=0.4, max_tokens=4096,
        stage="spravochnik_writer", item_id=item["id"],
    )
    meta, body = split_front_matter(answer)  # ValueError решает вызывающий (run.py)

    before = body
    body = _strip_unconfirmed_sentences(body)  # вырезать неподтверждённые утверждения ({{fact_check}})
    if body != before:
        log.info("справочник %s: вырезаны неподтверждённые утверждения ({{fact_check}})", item["slug"])
    body = add_restricted_org_footnotes(body)  # сноска о запрещённых в РФ (комплаенс РКН)
    _finalize_meta(meta, item)

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    # slug из очереди уникален по определению; ensure_unique — страховка от коллизии
    # с уже опубликованным (напр. если термин переехал).
    published_slugs = {r.get("slug") for r in state.load_published() if r.get("slug")}
    slug = ensure_unique(generate_slug(item["slug"]), published_slugs - {item["slug"]})
    meta["slug"] = slug
    path = DRAFTS_DIR / f"{slug}.md"
    path.write_text(render_markdown(meta, body), encoding="utf-8")
    log.info("черновик справочника: %s (%d знаков, тип %s)", path.name, len(body), item["type"])
    return path
