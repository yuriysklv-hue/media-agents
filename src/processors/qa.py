"""QA Validator: финальная проверка черновика перед публикацией.

Уровень 1 — rules-based (схема, длины, slug, анти-ИИ-клише) — обязательный.
Уровень 2 — GLM-4-Flash, мягкая проверка стиля/фактов; при недоступности GLM
пропускается (пайплайн продолжает работу, см. ТЗ раздел 10).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..llm_client import LLMUnavailable, parse_json_response, pipeline_client
from ..utils.config import fill_prompt, load_config, load_prompt
from ..utils.frontmatter import split_front_matter
from ..utils.logger import get_logger
from ..utils.state import StateManager

log = get_logger("qa")

AI_CLICHE_PATTERNS = [
    r"почему это важно",
    r"это не\s+\w+,\s+а\s+\w+",
    r"в мире, где",
    r"далеко идущие последствия",
    r"игра меняется",
    r"перепишем правила",
    r"в условиях стремительно",
    r"меняющемся ландшафте",
    r"революцион(?:ный|ного|ным)",
    r"поистине трансформативн",
    r"нельзя переоценить",
]
_CLICHE_RE = [re.compile(p, re.IGNORECASE) for p in AI_CLICHE_PATTERNS]

REQUIRED_FIELDS = ("title", "description", "pubDate", "author", "category", "geo")

TITLE_MIN, TITLE_MAX = 50, 80
DESCRIPTION_MAX = 160
BODY_MIN_CHARS = 500
TAGS_MIN, TAGS_MAX = 3, 7

# Требования к длине по типам материалов (символы тела с пробелами).
BODY_LIMITS = {"news": (500, 6000), "digest": (2000, 20000)}


_Q_OPEN_RE = re.compile(r"<q(\s[^>]*)?>", re.IGNORECASE)


def check_quote_markup(body: str) -> list[str]:
    """Валидность разметки прямой речи `<q cite="…">` (задача 7-E, формат B).

    Не FAIL: кривой `<q>` не ломает сборку Astro (сырой HTML в `.md` рендерится
    как есть), поэтому возвращаем только warnings — чтобы битую разметку было
    видно в логе, но материал из-за неё не терялся.
    """
    warnings: list[str] = []
    opens = _Q_OPEN_RE.findall(body)
    closes = body.count("</q>")
    if len(opens) != closes:
        warnings.append(
            f"разметка цитат: {len(opens)} <q> и {closes} </q> — теги не сбалансированы"
        )
    without_cite = sum(1 for attrs in opens if "cite=" not in (attrs or "").lower())
    if without_cite:
        warnings.append(
            f"разметка цитат: {without_cite} <q> без cite= (нужен URL первоисточника)"
        )
    return warnings


@dataclass
class QAResult:
    status: str = "PASS"  # PASS | FAIL
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Конкретные замечания LLM-QA (стиль/факты/тон) — питают петлю переписывания
    # в stage_write (задача 4). retryable_style=True, когда rules прошли, а завернул
    # именно стиль/тон/факты → материал имеет смысл переписать по замечаниям.
    llm_issues: list[str] = field(default_factory=list)
    retryable_style: bool = False

    def fail(self, message: str) -> None:
        self.status = "FAIL"
        self.errors.append(message)


def _parse_pub_date(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def check_rules(meta: dict, body: str, existing_slugs: set[str], slug: str,
                article_type: str = "news") -> QAResult:
    """Уровень 1: rules-based проверки. Все нарушения из таблицы ТЗ 7.7 → FAIL."""
    result = QAResult()
    vocab = load_config("vocabulary")

    for fld in REQUIRED_FIELDS:
        if not meta.get(fld):
            result.fail(f"обязательное поле «{fld}» отсутствует или пустое")
    if result.errors:
        return result  # дальше проверять нечего

    title = str(meta["title"])
    if not (TITLE_MIN <= len(title) <= TITLE_MAX):
        result.fail(f"title {len(title)} символов (нужно {TITLE_MIN}-{TITLE_MAX})")

    if len(str(meta["description"])) > DESCRIPTION_MAX:
        result.fail(f"description длиннее {DESCRIPTION_MAX} символов")

    pub = _parse_pub_date(meta["pubDate"])
    if pub is None:
        result.fail("pubDate не парсится как ISO 8601")
    elif pub > datetime.now(timezone.utc) + timedelta(minutes=15):
        result.fail("pubDate в будущем")

    if meta["category"] not in vocab["categories"]:
        result.fail(f"категория «{meta['category']}» вне словаря")

    geo = meta.get("geo")
    if not isinstance(geo, list) or not geo or any(g not in vocab["geo_values"] for g in geo):
        result.fail(f"geo «{geo}» — не массив значений из {vocab['geo_values']}")

    tags = meta.get("tags") or []
    known = {t.lower() for t in vocab["tags"]}
    if not (TAGS_MIN <= len(tags) <= TAGS_MAX):
        result.warnings.append(f"тегов {len(tags)} (рекомендовано {TAGS_MIN}-{TAGS_MAX})")
    for tag in tags:
        if str(tag).lower() not in known and not str(tag).endswith("[new]"):
            result.warnings.append(f"тег «{tag}» вне словаря и без пометки [new]")

    result.warnings.extend(check_quote_markup(body))

    body_min, body_max = BODY_LIMITS.get(article_type, (BODY_MIN_CHARS, 10**6))
    if len(body) < body_min:
        result.fail(f"тело {len(body)} символов (минимум {body_min} для {article_type})")
    elif len(body) > body_max:
        result.warnings.append(f"тело {len(body)} символов — длиннее ориентира {body_max}")

    source = meta.get("source")
    primary_url = ""
    if article_type == "news":
        primary_url = (source or {}).get("url", "") if isinstance(source, dict) else ""
        if not str(primary_url).startswith(("http://", "https://")):
            result.fail("source.url отсутствует или не http(s)")

    # additional_sources опционально; но если есть — структура строгая (иначе
    # строгая схема Astro уронит сборку сайта). Пустой список = поля нет.
    extras = meta.get("additional_sources")
    if extras is not None:
        if not isinstance(extras, list) or not extras:
            result.fail("additional_sources — не непустой список")
        else:
            for i, s in enumerate(extras):
                url = s.get("url", "") if isinstance(s, dict) else ""
                if not isinstance(s, dict) or not s.get("title"):
                    result.fail(f"additional_sources[{i}] без title/не объект")
                elif not str(url).startswith(("http://", "https://")):
                    result.fail(f"additional_sources[{i}].url не http(s)")
                elif url == primary_url:
                    result.fail(f"additional_sources[{i}] дублирует primary source")

    if slug in existing_slugs:
        result.fail(f"slug «{slug}» уже занят")

    for pattern in _CLICHE_RE:
        m = pattern.search(body) or pattern.search(title)
        if m:
            result.fail(f"анти-ИИ-паттерн: «{m.group(0)}»")

    return result


def check_style_llm(body: str, source_content: str, state: StateManager) -> QAResult:
    """Уровень 2: мягкая LLM-проверка стиля/фактов/тона."""
    result = QAResult()
    try:
        client, model = pipeline_client("qa_style", state)
    except LLMUnavailable:
        result.warnings.append("GLM недоступен — LLM-проверка стиля пропущена")
        return result

    prompt = fill_prompt(
        load_prompt("qa_style"), body=body[:6000], source_content=source_content[:6000]
    )
    try:
        answer = client.chat(
            model=model, system="", user=prompt,
            temperature=0.0, max_tokens=600,
            response_format={"type": "json_object"}, stage="qa",
        )
        verdict = parse_json_response(answer)
    except Exception as exc:
        result.warnings.append(f"LLM-проверка не отработала: {exc}")
        return result

    issues = verdict.get("issues") or []
    result.llm_issues = [str(i) for i in issues if str(i).strip()]
    for key, label in (("style_ok", "стиль"), ("facts_ok", "факты"), ("tone_ok", "тон")):
        if verdict.get(key) is False:
            result.fail(f"LLM QA: {label} не прошёл ({'; '.join(result.llm_issues) or 'без деталей'})")
    return result


def run_qa(path: Path, state: StateManager, source_content: str = "",
          article_type: str = "news") -> QAResult:
    """Полный QA черновика. FAIL — файл уходит в data/drafts/failed/."""
    meta, body = split_front_matter(path.read_text(encoding="utf-8"))
    existing = {rec["slug"] for rec in state.load_published() if rec.get("slug")}

    result = check_rules(meta, body, existing, slug=path.stem, article_type=article_type)
    if result.status == "PASS":
        llm = check_style_llm(body, source_content, state)
        result.errors += llm.errors
        result.warnings += llm.warnings
        result.llm_issues = llm.llm_issues
        if llm.status == "FAIL":
            result.status = "FAIL"
            # Rules прошли, завернул стиль/тон/факты → материал переписываем по замечаниям.
            result.retryable_style = True

    if result.status == "FAIL":
        # Персист брака: запись с причиной уходит в failed_drafts.jsonl (коммитится
        # воркфлоу) — размеченный корпус «не прошло + почему» для тюнинга промптов.
        # ArticleStore несёт add_failed_draft; голый StateManager — нет (тесты),
        # поэтому мягко через getattr.
        add_failed = getattr(state, "add_failed_draft", None)
        if add_failed is not None:
            add_failed({
                "slug": path.stem,
                "type": article_type,
                "title": meta.get("title", ""),
                "reasons": result.errors,
                "llm_issues": result.llm_issues,
                "body": body,
            })
        failed_dir = path.parent.parent / "failed"
        failed_dir.mkdir(parents=True, exist_ok=True)
        target = failed_dir / path.name
        path.rename(target)
        log.warning("QA FAIL %s: %s (файл → drafts/failed/)", path.name, "; ".join(result.errors))
    else:
        for w in result.warnings:
            log.info("QA warning %s: %s", path.name, w)
    return result
