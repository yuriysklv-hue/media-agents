"""Fact-checker: сверка ключевых фактов материала с Wikipedia (GLM-4-Flash).

Best-effort по замыслу: надёжен только для company/organization (у них есть
статья Wikipedia). Для technology/term Wikipedia часто пуста → фактчек не
блокирует, а помечает материал на ручное ревью. critical_error → needs_fix.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..llm_client import LLMUnavailable, parse_json_response, pipeline_client
from ..utils.config import fill_prompt, load_prompt
from ..utils.frontmatter import split_front_matter
from ..utils.logger import get_logger
from ..utils.state import StateManager

log = get_logger("spravochnik.factcheck")


@dataclass
class FactResult:
    passed: bool = True
    warnings: list[str] = field(default_factory=list)
    critical_errors: list[str] = field(default_factory=list)


def check_facts(path: Path, research: dict, state: StateManager) -> FactResult:
    """Сверяет facts материала с Wikipedia extract. critical_errors → passed=False."""
    result = FactResult()
    wiki = research.get("wikipedia")
    if not wiki or not wiki.get("extract"):
        result.warnings.append("нет опорной статьи Wikipedia — факты не сверены, нужно ручное ревью")
        return result

    meta, _ = split_front_matter(path.read_text(encoding="utf-8"))
    facts = meta.get("facts") or {}

    try:
        client, model = pipeline_client("spravochnik_fact_checker", state)
    except LLMUnavailable:
        result.warnings.append("GLM недоступен — фактчек пропущен")
        return result

    prompt = fill_prompt(
        load_prompt("spravochnik_fact_checker"),
        material_facts=json.dumps(facts, ensure_ascii=False),
        wikipedia_extract=wiki["extract"][:4000],
    )
    try:
        answer = client.chat(
            model=model, system="", user=prompt,
            temperature=0.0, max_tokens=800,
            response_format={"type": "json_object"}, stage="spravochnik_factcheck",
        )
        verdict = parse_json_response(answer)
    except Exception as exc:  # noqa: BLE001 — фактчек мягкий, не роняем пайплайн
        result.warnings.append(f"фактчек не отработал: {exc}")
        return result

    result.critical_errors = [str(e) for e in (verdict.get("critical_errors") or []) if str(e).strip()]
    result.warnings += [str(w) for w in (verdict.get("warnings") or []) if str(w).strip()]
    result.passed = not result.critical_errors
    log.info("фактчек %s: passed=%s, critical=%d, warnings=%d",
             path.name, result.passed, len(result.critical_errors), len(result.warnings))
    return result
