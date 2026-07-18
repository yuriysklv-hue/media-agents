"""Общий доступ к репозиторию media: клон на свежий main + пути коллекций.

Вынесено из git_publisher, чтобы и публишер, и дайджест-райтер работали с одним
клоном (media-clone/) и одним авторизованным URL. Публишер импортирует отсюда —
поведение не меняется (обратная совместимость). Дайджест читает тот же клон,
просто раньше и только на чтение.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..utils.config import ROOT

CLONE_DIR = ROOT / "media-clone"


def _git_env() -> dict:
    token = os.environ.get("GH_TOKEN", "").strip()
    repo = os.environ.get("MEDIA_REPO", "").strip()
    url = os.environ.get("MEDIA_GIT_URL", "").strip()
    if not url:
        if not (token and repo):
            raise RuntimeError("нужны GH_TOKEN + MEDIA_REPO (или MEDIA_GIT_URL)")
        url = f"https://x-access-token:{token}@github.com/{repo}.git"
    return {"token": token, "repo": repo, "url": url,
            "subdir": os.environ.get("MEDIA_SITE_SUBDIR", "media-site").strip()}


def _prepare_clone(git_url: str):
    """Свежий клон media (или fetch+reset, если клон уже есть)."""
    from git import Repo

    if CLONE_DIR.exists():
        repo = Repo(CLONE_DIR)
        repo.remotes.origin.set_url(git_url)
        repo.remotes.origin.fetch("main")
        repo.git.checkout("main")
        repo.git.reset("--hard", "origin/main")
    else:
        repo = Repo.clone_from(git_url, CLONE_DIR, branch="main", depth=1)
    return repo


def clone_or_update_media() -> Path:
    """Поднимает клон media на свежем main и возвращает путь к нему.

    Бросает исключение, если нет доступа (GH_TOKEN/MEDIA_REPO) или сеть недоступна —
    вызывающий (дайджест) на этом падает и не выпускает дайджест: лучше нет дайджеста,
    чем дайджест с битыми ссылками.
    """
    cfg = _git_env()
    _prepare_clone(cfg["url"])
    return CLONE_DIR


def news_dir(clone: Path) -> Path:
    """Папка живой коллекции новостей сайта в клоне media."""
    subdir = os.environ.get("MEDIA_SITE_SUBDIR", "media-site").strip()
    return clone / subdir / "src" / "content" / "news"
