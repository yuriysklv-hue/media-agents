"""Пути проекта и загрузка YAML-конфигов."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
PROMPTS_DIR = ROOT / "src" / "prompts"

load_dotenv(ROOT / ".env")


@lru_cache(maxsize=None)
def load_config(name: str) -> dict:
    """Читает config/<name>.yaml (кэшируется на процесс)."""
    with open(CONFIG_DIR / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Читает src/prompts/<name>.md."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def fill_prompt(template: str, **values: str) -> str:
    """Подставляет {key} → value. Не str.format: в промптах есть литеральные {}."""
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def ensure_data_dirs() -> None:
    """Создаёт дерево data/ при первом запуске."""
    for sub in (
        "inbox",
        "drafts/news",
        "drafts/digest",
        "drafts/failed",
        "published",
        "state",
        "logs",
    ):
        (DATA_DIR / sub).mkdir(parents=True, exist_ok=True)


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")
