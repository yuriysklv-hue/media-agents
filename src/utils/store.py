"""ArticleStore — фасад над хранилищем состояния пайплайна.

Задача — единая точка доступа к «памяти» пайплайна (опубликованное, брак,
дедуп-корпус), за которой сегодня стоит файловый `StateManager` (JSONL в data/),
а завтра может встать PostgreSQL (Selectel) без правок вызывающего кода.

Реализация — КОМПОЗИЦИЯ, а не наследование: `ArticleStore` держит `StateManager`
и делегирует ему всё неизвестное через `__getattr__`. Это делает `ArticleStore`
drop-in заменой `StateManager` в сигнатурах (утиная типизация: `state.log_llm_usage`,
`state.load_published`, `state.state_dir` и т.п. продолжают работать), но новую
персистентность (`failed_drafts`, дедуп-корпус) вводит своими методами. При
переезде на БД подменяется только тело этих методов и делегата.

Старый код с `StateManager` не трогаем (обратная совместимость); новый —
конструирует и передаёт `ArticleStore`.
"""
from __future__ import annotations

from pathlib import Path

from .config import DATA_DIR
from .state import StateManager, append_jsonl, read_jsonl, utcnow_iso


class ArticleStore:
    def __init__(self, state: StateManager | None = None, data_dir: Path = DATA_DIR):
        self.state = state or StateManager(data_dir)

    def __getattr__(self, name: str):
        # Всё, чего нет на ArticleStore, обслуживает StateManager (load_published,
        # save_seen_urls, log_llm_usage, state_dir, published_dir, …).
        # __getattr__ зовётся только при отсутствии атрибута — свои методы имеют
        # приоритет. self.state достаём из __dict__, чтобы не уйти в рекурсию.
        return getattr(self.__dict__["state"], name)

    # --- реестр брака (замена эфемерной директории drafts/failed/*.md) ---

    @property
    def failed_drafts_path(self) -> Path:
        # В data/published/ — коммитится шагом воркфлоу «Commit data state»,
        # значит корпус брака переживает эфемерный раннер (задача 2 из бэклога).
        return self.state.published_dir / "failed_drafts.jsonl"

    def add_failed_draft(self, record: dict) -> None:
        """Дописывает забракованный QA черновик с причиной — размеченный корпус
        «не прошло + почему» для тюнинга промптов."""
        record.setdefault("logged_at", utcnow_iso())
        append_jsonl(self.failed_drafts_path, record)

    def load_failed_drafts(self) -> list[dict]:
        return read_jsonl(self.failed_drafts_path)

    # --- дедуп-корпус опубликованного ---

    def published_titles(self) -> list[dict]:
        """[{slug, title}] опубликованных — корпус для отсева дублей по заголовку."""
        return [
            {"slug": r.get("slug", ""), "title": str(r.get("title", ""))}
            for r in self.state.load_published()
            if r.get("title")
        ]
