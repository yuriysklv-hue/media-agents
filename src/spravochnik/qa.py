"""Rules-QA уровень 1 для справочника (детерминированный, обязательный).

По образцу processors/qa.check_rules. Ловит то, что уронит строгую схему Astro
или выдаст брак: битый YAML/поля, мусор-кодировку (иероглифы из копипаста —
примеры исходного ТЗ их содержали), отсутствие обязательных facts/разделов,
thin-content. Фактчек (уровень 2) — отдельно, он про истинность, не про форму.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..utils.frontmatter import split_front_matter
from ..utils.logger import get_logger
from ..utils.state import StateManager

log = get_logger("spravochnik.qa")

DESCRIPTION_MAX = 160
BODY_MIN_CHARS = 500

VALID_TYPES = ("company", "technology", "term", "organization")

# Обязательные поля facts по типу (раздел 2 ТЗ).
REQUIRED_FACTS = {
    "company": ("founded", "founders", "hq", "official_url"),
    "technology": ("developer", "category", "official_url"),
    "term": ("category", "definition"),
    "organization": ("full_name", "founded", "mission", "official_url"),
}

# Обязательные разделы тела по типу (первые слова заголовков ## …).
REQUIRED_SECTIONS = {
    "company": ("О компании", "История", "Роль", "Ключевые продукты"),
    "technology": ("Что это", "Как работает", "Для кого", "Альтернативы"),
    "term": ("Определение", "Контекст", "Примеры"),
    "organization": ("Что это", "Ключевые инициативы", "Значимость"),
}

# Мусор-кодировка: китайские/японские/корейские символы в русском тексте —
# признак копипаста из необработанного источника (保留/视频/指向 в примерах ТЗ).
# CJK-пунктуация + CJK Ext-A + Unified Ideographs + Hangul.
_CJK_RE = re.compile("[　-〿㐀-鿿가-힯]")
_HEADING_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


@dataclass
class QAResult:
    status: str = "PASS"  # PASS | FAIL
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.status = "FAIL"
        self.errors.append(message)


def _parse_pub_date(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def check_rules(meta: dict, body: str, existing_slugs: set[str], slug: str) -> QAResult:
    result = QAResult()

    mtype = meta.get("type")
    if mtype not in VALID_TYPES:
        result.fail(f"type «{mtype}» не из {VALID_TYPES}")
        return result  # без типа дальше проверять нечего

    if not str(meta.get("title", "")).strip():
        result.fail("title пустой")
    description = str(meta.get("description", ""))
    if not description.strip():
        result.fail("description пустой")
    elif len(description) > DESCRIPTION_MAX:
        result.fail(f"description длиннее {DESCRIPTION_MAX} символов ({len(description)})")

    pub = _parse_pub_date(meta.get("pubDate"))
    if pub is None:
        result.fail("pubDate не парсится как ISO 8601")
    elif pub > datetime.now(timezone.utc) + timedelta(minutes=15):
        result.fail("pubDate в будущем")

    facts = meta.get("facts") or {}
    if not isinstance(facts, dict):
        result.fail("facts — не объект")
    else:
        for fld in REQUIRED_FACTS.get(mtype, ()):
            if not facts.get(fld):
                result.fail(f"facts.{fld} обязателен для типа {mtype}, но пуст")

    headings = {h.strip().split()[0] if h.strip() else "" for h in _HEADING_RE.findall(body)}
    present = "\n".join(_HEADING_RE.findall(body))
    for section in REQUIRED_SECTIONS.get(mtype, ()):
        if section not in present:
            result.fail(f"нет обязательного раздела «## {section}» для типа {mtype}")

    if len(body) < BODY_MIN_CHARS:
        result.fail(f"тело {len(body)} символов (минимум {BODY_MIN_CHARS} — анти-thin-content)")

    junk = _CJK_RE.findall(body) + _CJK_RE.findall(str(meta.get("title", "")))
    if junk:
        result.fail(f"мусор-кодировка в тексте (CJK-символы): {''.join(junk[:5])}")

    tags = meta.get("tags") or []
    if not (3 <= len(tags) <= 7):
        result.warnings.append(f"тегов {len(tags)} (рекомендовано 3–7)")

    if slug in existing_slugs:
        result.fail(f"slug «{slug}» уже занят")

    return result


def run_qa(path: Path, state: StateManager) -> QAResult:
    """Полный rules-QA черновика справочника. FAIL — файл → data/spravochnik/failed/."""
    meta, body = split_front_matter(path.read_text(encoding="utf-8"))
    # slug справочника не должен коллизить с уже опубликованным справочником
    # (у новостей своё пространство имён — их slug'и не учитываем).
    existing = {r["slug"] for r in state.load_published()
                if r.get("slug") and r.get("type") == "spravochnik"}
    result = check_rules(meta, body, existing, slug=path.stem)

    if result.status == "FAIL":
        add_failed = getattr(state, "add_failed_draft", None)
        if add_failed is not None:
            add_failed({
                "slug": path.stem, "type": "spravochnik",
                "title": meta.get("title", ""), "reasons": result.errors, "body": body,
            })
        failed_dir = path.parent.parent / "failed"
        failed_dir.mkdir(parents=True, exist_ok=True)
        path.rename(failed_dir / path.name)
        log.warning("QA FAIL %s: %s", path.name, "; ".join(result.errors))
    else:
        for w in result.warnings:
            log.info("QA warning %s: %s", path.name, w)
    return result
