"""Управление персистентным состоянием пайплайна (data/state, data/published)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def append_jsonl(path: Path, item: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def write_jsonl(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


class StateManager:
    """Чтение/запись state-файлов. Все пути — внутри data/."""

    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.state_dir = data_dir / "state"
        self.published_dir = data_dir / "published"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.published_dir.mkdir(parents=True, exist_ok=True)

    # --- seen URLs (дедупликация на уровне коллектора) ---

    @property
    def _seen_urls_path(self) -> Path:
        return self.state_dir / "seen_urls.json"

    def load_seen_urls(self) -> set[str]:
        if not self._seen_urls_path.exists():
            return set()
        data = json.loads(self._seen_urls_path.read_text(encoding="utf-8"))
        return set(data.get("urls", []))

    def save_seen_urls(self, urls: set[str]) -> None:
        payload = {"last_updated": utcnow_iso(), "urls": sorted(urls)}
        self._seen_urls_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    # --- кэш переводов (id → переведённые поля) ---

    @property
    def _translation_cache_path(self) -> Path:
        return self.state_dir / "translation_cache.json"

    def load_translation_cache(self) -> dict[str, dict]:
        if not self._translation_cache_path.exists():
            return {}
        return json.loads(self._translation_cache_path.read_text(encoding="utf-8"))

    def save_translation_cache(self, cache: dict[str, dict]) -> None:
        self._translation_cache_path.write_text(
            json.dumps(cache, ensure_ascii=False), encoding="utf-8"
        )

    # --- embeddings опубликованных материалов ---

    @property
    def _embeddings_path(self) -> Path:
        return self.published_dir / "embeddings.npz"

    def load_published_embeddings(self) -> tuple["object", list[str]]:
        """Возвращает (матрица NxD или None, список slug'ов)."""
        import numpy as np

        if not self._embeddings_path.exists():
            return None, []
        data = np.load(self._embeddings_path, allow_pickle=False)
        return data["vectors"], [str(s) for s in data["slugs"]]

    def add_published_embeddings(self, vectors: list[list[float]], slugs: list[str]) -> None:
        import numpy as np

        if not vectors:
            return
        new = np.asarray(vectors, dtype=np.float32)
        existing, existing_slugs = self.load_published_embeddings()
        if existing is not None and len(existing_slugs) > 0:
            vectors_all = np.vstack([existing, new])
            slugs_all = existing_slugs + list(slugs)
        else:
            vectors_all, slugs_all = new, list(slugs)
        np.savez_compressed(
            self._embeddings_path,
            vectors=vectors_all,
            slugs=np.asarray(slugs_all, dtype=object).astype(str),
        )

    # --- реестр публикаций ---

    @property
    def published_path(self) -> Path:
        return self.published_dir / "published.jsonl"

    def load_published(self) -> list[dict]:
        return read_jsonl(self.published_path)

    def log_published(self, record: dict) -> None:
        append_jsonl(self.published_path, record)

    # --- учёт LLM-вызовов ---

    def log_llm_usage(
        self,
        stage: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        item_id: str | None = None,
    ) -> None:
        append_jsonl(
            self.state_dir / "llm_usage.jsonl",
            {
                "timestamp": utcnow_iso(),
                "stage": stage,
                "provider": provider,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost_usd, 6),
                "item_id": item_id,
            },
        )

    # --- отметки о запусках этапов ---

    @property
    def _last_run_path(self) -> Path:
        return self.state_dir / "last_run.json"

    def get_last_run(self, stage: str) -> str | None:
        if not self._last_run_path.exists():
            return None
        return json.loads(self._last_run_path.read_text(encoding="utf-8")).get(stage)

    def set_last_run(self, stage: str, timestamp: str | None = None) -> None:
        data = {}
        if self._last_run_path.exists():
            data = json.loads(self._last_run_path.read_text(encoding="utf-8"))
        data[stage] = timestamp or utcnow_iso()
        self._last_run_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
