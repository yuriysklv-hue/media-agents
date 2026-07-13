"""Author News: оригинальная новость на русском по переведённым источникам.

Не перевод и не рерайт перевода — самостоятельный журналистский текст.
Перевод передаётся автору как рабочий материал для извлечения фактов.
"""
from __future__ import annotations

from pathlib import Path

from ..llm_client import pipeline_client
from ..utils.config import DATA_DIR, fill_prompt, load_prompt
from ..utils.frontmatter import render_markdown, split_front_matter
from ..utils.legal import add_restricted_org_footnotes
from ..utils.logger import get_logger
from ..utils.state import StateManager, utcnow_iso

log = get_logger("news_writer")

DRAFTS_DIR = DATA_DIR / "drafts" / "news"

# Синхронно с qa.py: QA бракует заголовок вне этого диапазона (задача 6).
TITLE_MIN, TITLE_MAX = 50, 80


def _sources_block(event: dict) -> str:
    sources = event["sources"]
    if len(sources) == 1:
        s = sources[0]
        return (
            "Источник (переведённый рабочий материал):\n"
            f"- Заголовок: {s['title_ru']}\n"
            f"- Текст: {s['content_ru'] or s['summary_ru']}\n"
            f"- Источник: {s['source_name']} ({s['source_url']})\n"
            f"- Оригинальный заголовок (для сверки фактов): {s['title_original']}"
        )
    lines = ["Источники (переведённый рабочий материал — синтезируй факты из всех):", ""]
    for n, s in enumerate(sources, 1):
        primary = " (primary)" if s.get("is_primary") else ""
        lines += [
            f"[{n}] {s['source_name']}{primary}",
            f"- Заголовок: {s['title_ru']}",
            f"- Текст: {s['content_ru'] or s['summary_ru']}",
            f"- Оригинальный заголовок: {s['title_original']}",
            f"- URL: {s['source_url']}",
            "",
        ]
    lines.append(
        "Ссылайся на primary source в front-matter. В тексте можешь ссылаться "
        "на дополнительные источники, если они добавляют факты."
    )
    return "\n".join(lines)


def _finalize_meta(meta: dict, event: dict, primary: dict) -> dict:
    """Детерминированная доводка front-matter после генерации модели.

    Источник — из сырья, а не из фантазии модели. `pubDate` — момент выхода
    материала на 1screen (UTC now), а НЕ дата публикации в источнике из RSS
    (`event["published_at"]`): иначе статьи датируются задним числом, выглядят
    несвежими, не зажигают live-точку на главной (isLive = моложе 3 ч) и тонут
    внизу ленты (сортировка по pubDate убыв.). Дата источника не теряется —
    паблишер сохраняет её в published.jsonl (`source_published_at`); во
    front-matter она не идёт, чтобы не расширять контракт схемы сайта.
    """
    meta["source"] = {"title": primary["source_name"], "url": primary["source_url"]}
    meta["pubDate"] = utcnow_iso()
    # Сид категории/geo по региону источника; Enricher уточнит и проставит автора.
    region = event.get("region", "world")
    meta.setdefault("category", "adtech-ru" if region == "ru" else "adtech-world")
    meta.setdefault("geo", ["РФ"] if region == "ru" else ["МИР"])
    return meta


def _title_len_ok(meta: dict) -> bool:
    return TITLE_MIN <= len(str(meta.get("title", ""))) <= TITLE_MAX


def _retry_hint(title: str) -> str:
    """Корректирующая приписка к промпту при промахе по длине заголовка."""
    n = len(title)
    fix = "слишком короткий — удлини" if n < TITLE_MIN else "слишком длинный — сократи"
    return (
        f"\n\nВНИМАНИЕ: предыдущий заголовок был {n} символов ({fix} до диапазона "
        f"{TITLE_MIN}–{TITLE_MAX}). Заголовок: «{title}». Перепиши материал так, "
        f"чтобы title был строго {TITLE_MIN}–{TITLE_MAX} символов."
    )


def _feedback_hint(issues: str) -> str:
    """Приписка к промпту при переписывании материала по замечаниям QA (задача 4).

    Первая версия завернута редактурой за «ИИ-голос» — скармливаем конкретные
    замечания и просим переписать заново, сохранив факты. Даём свободу структуры,
    чтобы не воспроизвести тот же шаблон.
    """
    return (
        "\n\nВНИМАНИЕ: предыдущая версия этого материала отклонена редактурой за "
        "качество текста. Замечания редактора:\n"
        f"{issues}\n"
        "Перепиши материал ЗАНОВО, устранив каждое замечание: живее, с авторской "
        "интонацией, конкретно, без гладких общих мест и ИИ-шаблонов. Меняй структуру "
        "и заходы — не повторяй прежний каркас. Факты, цифры и ссылки сохрани точными, "
        "заголовок держи в 50–80 символах."
    )


def write_news(event: dict, state: StateManager, feedback: str | None = None) -> Path:
    """Пишет черновик data/drafts/news/draft-{event_id}.md (slug проставит Enricher).

    feedback — замечания QA с прошлого прохода: при повторной генерации материал
    переписывается с их учётом (петля восстановления «ИИ-голоса», задача 4).
    """
    client, model = pipeline_client("news_writer", state)
    primary = next(s for s in event["sources"] if s.get("is_primary"))

    prompt = fill_prompt(
        load_prompt("news_writer"),
        sources_block=_sources_block(event),
        primary_source_name=primary["source_name"],
        primary_source_url=primary["source_url"],
    )
    if feedback:
        prompt += _feedback_hint(feedback)

    def _generate(user_prompt: str) -> tuple[dict, str]:
        answer = client.chat(
            model=model, system="", user=user_prompt,
            temperature=0.7, max_tokens=4096,
            stage="news_writer", item_id=event["event_id"],
        )
        # ValueError → событие в drafts/failed решает вызывающий.
        return split_front_matter(answer)

    meta, body = _generate(prompt)
    # Авто-ретрай при промахе по длине заголовка — иначе QA гарантированно
    # забракует материал (задача 6). Одна попытка: дороже смысла нет.
    if not _title_len_ok(meta):
        title = str(meta.get("title", ""))
        log.info("заголовок %d симв. вне %d–%d — авто-ретрай", len(title), TITLE_MIN, TITLE_MAX)
        try:
            retry_meta, retry_body = _generate(prompt + _retry_hint(title))
            if _title_len_ok(retry_meta):
                meta, body = retry_meta, retry_body
        except ValueError as exc:  # ретрай без front-matter — оставляем первый вариант
            log.warning("ретрай заголовка не распарсился (%s) — беру первый вариант", exc)

    body = add_restricted_org_footnotes(body)  # сноска о запрещённых в РФ организациях
    _finalize_meta(meta, event, primary)

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    path = DRAFTS_DIR / f"draft-{event['event_id']}.md"
    path.write_text(render_markdown(meta, body), encoding="utf-8")
    log.info("черновик написан: %s (%d знаков)", path.name, len(body))
    return path
