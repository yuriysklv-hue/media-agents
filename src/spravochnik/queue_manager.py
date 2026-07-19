"""Queue Manager: работа с config/spravochnik_queue.yaml.

Очередь — единственный триггер пайплайна справочника (RSS нет). Владелец правит
её вручную (через Claude Code или CLI-обёртку); пайплайн читает следующий
pending|revision и пишет статусы обратно. Персист — шаг «Commit queue state»
в spravochnik.yml (иначе запись = 403, урок задачи 7 новостного пайплайна).
"""
from __future__ import annotations

import yaml

from ..utils.config import CONFIG_DIR
from ..utils.logger import get_logger
from ..utils.state import utcnow_iso

log = get_logger("spravochnik.queue")

QUEUE_PATH = CONFIG_DIR / "spravochnik_queue.yaml"

# Типы контента и обязательное поле-дискриминатор для каждого (кроме term, где
# category, но и developer/subtype не обязателен на уровне очереди).
VALID_TYPES = ("company", "technology", "term", "organization")

# Статусы, которые пайплайн забирает в работу (по порядку в очереди).
ACTIONABLE = ("pending", "revision")


def load_queue(path=QUEUE_PATH) -> list[dict]:
    """Читает items из очереди. Нет файла / пусто → []."""
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("items") or [])


def save_queue(items: list[dict], path=QUEUE_PATH) -> None:
    """Пишет items обратно, сохраняя человекочитаемость (unicode, порядок полей)."""
    header = (
        "# Очередь материалов Базы знаний (Spravochnik).\n"
        "# Владелец добавляет термины вручную; пайплайн пишет статусы обратно.\n"
        "# Lifecycle: pending → writing → review → published (ветки: revision, needs_fix, skipped)\n"
        "# ТЗ: для_кодинга/ТЗ_База_знаний.md\n\n"
    )
    body = yaml.dump(
        {"items": items}, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000
    )
    path.write_text(header + body, encoding="utf-8")


def get_next_item(items: list[dict] | None = None) -> dict | None:
    """Первый айтем со статусом pending|revision (в порядке очереди)."""
    items = items if items is not None else load_queue()
    for item in items:
        if item.get("status") in ACTIONABLE:
            return item
    return None


def find_item(item_id: str, items: list[dict]) -> dict | None:
    return next((i for i in items if i.get("id") == item_id), None)


def update_status(item_id: str, status: str, path=QUEUE_PATH, **fields) -> list[dict]:
    """Ставит статус (и любые доп. поля: feedback, iteration, pr_url, pr_number,
    published_at) айтему по id и сохраняет очередь. Возвращает новый список items."""
    items = load_queue(path)
    item = find_item(item_id, items)
    if item is None:
        raise KeyError(f"айтем «{item_id}» не найден в очереди")
    item["status"] = status
    item.update(fields)
    save_queue(items, path)
    log.info("очередь: %s → %s%s", item_id, status,
             f" ({fields})" if fields else "")
    return items


def add_item(term: str, type: str, path=QUEUE_PATH, *, slug: str | None = None,
             **fields) -> dict:
    """Добавляет новый айтем со status=pending, iteration=0. Slug уникален в очереди."""
    from ..utils.slug import ensure_unique, generate_slug

    if type not in VALID_TYPES:
        raise ValueError(f"тип «{type}» не из {VALID_TYPES}")
    items = load_queue(path)
    taken = {i.get("slug") for i in items if i.get("slug")}
    base_slug = generate_slug(slug or term)
    unique_slug = ensure_unique(base_slug, taken)
    item = {
        "id": unique_slug,
        "term": term,
        "type": type,
        "status": "pending",
        "iteration": 0,
        "feedback": None,
        "created_at": utcnow_iso()[:10],
        "published_at": None,
        "slug": unique_slug,
        **fields,
    }
    items.append(item)
    save_queue(items, path)
    log.info("очередь: +айтем %s (%s)", unique_slug, type)
    return item
