"""Translator: рабочий перевод title/summary/content на русский (DeepSeek-V3).

Перевод — промежуточный материал для Author News, в финальный файл не попадает.
Кэш переводов (data/state/translation_cache.json) предотвращает повторную оплату.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..llm_client import LLMUnavailable, parse_json_response, pipeline_client
from ..utils.config import DATA_DIR, fill_prompt, load_prompt
from ..utils.logger import get_logger
from ..utils.state import StateManager, read_jsonl, utcnow_iso, write_jsonl

log = get_logger("translator")

PASSED_PATH = DATA_DIR / "inbox" / "passed_pre_filter.jsonl"
TRANSLATED_PATH = DATA_DIR / "inbox" / "translated_items.jsonl"

# Порог переключения на reasoner: ~8K токенов ≈ 30K символов английского текста.
LONG_TEXT_CHARS = 30_000


@dataclass
class TranslateResult:
    total: int = 0
    translated: int = 0
    from_cache: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def _translate_one(client, model_short: str, model_long: str, item: dict) -> dict:
    template = load_prompt("translator")
    content = item.get("content") or item.get("summary") or ""
    prompt = fill_prompt(
        template,
        title=item.get("title", ""),
        summary=item.get("summary", ""),
        content=content,
    )
    model = model_long if len(content) > LONG_TEXT_CHARS else model_short
    answer = client.chat(
        model=model, system="", user=prompt,
        temperature=0.2, max_tokens=8192,
        response_format={"type": "json_object"},
        stage="translator", item_id=item["id"],
    )
    data = parse_json_response(answer)
    return {
        "title_ru": str(data.get("title_ru", "")).strip(),
        "summary_ru": str(data.get("summary_ru", "")).strip(),
        "content_ru": str(data.get("content_ru", "")).strip(),
        "translator_model": model,
    }


def run_translator(state: StateManager) -> TranslateResult:
    """passed_pre_filter.jsonl → translated_items.jsonl."""
    result = TranslateResult()
    items = read_jsonl(PASSED_PATH)
    result.total = len(items)
    if not items:
        write_jsonl(TRANSLATED_PATH, [])
        return result

    from ..llm_client import resolve_model
    from ..utils.config import load_config

    config = load_config("models")
    try:
        client, model_short = pipeline_client("translator", state)
        _, model_long = resolve_model(config["pipeline"].get("translator_long", "deepseek:reasoner"), config)
    except LLMUnavailable as exc:
        # Без переводчика пайплайн не имеет смысла — останавливаемся (см. ТЗ, раздел 10).
        raise RuntimeError(f"DeepSeek недоступен: {exc}. Пайплайн остановлен.") from exc

    cache = state.load_translation_cache()
    translated = []
    for item in items:
        item = dict(item)
        if item.get("language") == "ru":
            item.update(
                title_ru=item["title"], summary_ru=item["summary"],
                content_ru=item.get("content", ""), translator_model=None,
            )
            item["translated_at"] = utcnow_iso()
            translated.append(item)
            continue

        cached = cache.get(item["id"])
        if cached:
            item.update(cached)
            item["translated_at"] = utcnow_iso()
            translated.append(item)
            result.from_cache += 1
            continue

        try:
            fields = _translate_one(client, model_short, model_long, item)
        except Exception as exc:
            log.warning("перевод %s (%s) не удался: %s", item["id"], item["title"][:60], exc)
            result.failed += 1
            result.errors.append(f"{item['id']}: {exc}")
            continue
        if not fields["title_ru"]:
            log.warning("пустой перевод для %s — пропускаем", item["id"])
            result.failed += 1
            continue
        cache[item["id"]] = fields
        item.update(fields)
        item["translated_at"] = utcnow_iso()
        translated.append(item)
        result.translated += 1

    state.save_translation_cache(cache)
    write_jsonl(TRANSLATED_PATH, translated)
    log.info("переведено %d (из кэша %d, ошибок %d)",
             result.translated, result.from_cache, result.failed)
    return result
