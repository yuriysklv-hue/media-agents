"""Publisher: ветка + draft-PR в репо media с готовым .md справочника.

Переиспользует media_repo (клон + авторизованный URL), как новостной публишер.
Файл кладётся в {subdir}/src/content/spravochnik/{slug}.md. PR создаётся как
draft (черновик) — материал проходит ручное ревью перед merge (разд. 4 ТЗ).
В DRY_RUN git-операции пропускаются, черновик остаётся в data/spravochnik/drafts/.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import requests

from ..publishers.media_repo import CLONE_DIR, _git_env, _prepare_clone
from ..utils.config import env_flag
from ..utils.frontmatter import split_front_matter
from ..utils.logger import get_logger

log = get_logger("spravochnik.publisher")

GITHUB_API = "https://api.github.com"
CONTENT_SUBPATH = ("src", "content", "spravochnik")


@dataclass
class PublishResult:
    pr_url: str | None = None
    pr_number: int | None = None
    branch: str | None = None
    dry_run: bool = False


def _create_draft_pr(cfg: dict, branch: str, title: str, body: str) -> dict:
    resp = requests.post(
        f"{GITHUB_API}/repos/{cfg['repo']}/pulls",
        headers={"Authorization": f"Bearer {cfg['token']}",
                 "Accept": "application/vnd.github+json"},
        json={"title": title, "head": branch, "base": "main", "body": body, "draft": True},
        timeout=30,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"PR не создан ({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    return {"url": data["html_url"], "number": data["number"]}


def _pr_body(item: dict, path: Path) -> str:
    meta, _ = split_front_matter(path.read_text(encoding="utf-8"))
    return (
        f"## База знаний: {meta.get('title', item['term'])}\n\n"
        f"- Тип: `{item['type']}`\n"
        f"- Slug: `{item['slug']}` → `/spravochnik/{item['slug']}`\n"
        f"- Итерация: {item.get('iteration', 0)}\n"
        f"- Файл: `src/content/spravochnik/{path.name}`\n\n"
        f"> Черновик (draft). Merge публикует материал; для доработки — верните "
        f"айтем `{item['id']}` на revision через Claude Code.\n"
    )


def publish(item: dict, path: Path) -> PublishResult:
    """Кладёт файл в клон media, пушит ветку spravochnik/{slug}, открывает draft-PR."""
    result = PublishResult(branch=f"spravochnik/{item['slug']}")

    if env_flag("DRY_RUN"):
        result.dry_run = True
        log.info("DRY_RUN: PR не создаём, черновик остаётся в %s", path)
        return result

    cfg = _git_env()
    repo = _prepare_clone(cfg["url"])
    repo.git.checkout("-B", result.branch)

    target_dir = CLONE_DIR / cfg["subdir"] / Path(*CONTENT_SUBPATH)
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target_dir / path.name)

    repo.git.add(A=True)
    if not repo.is_dirty(untracked_files=True):
        log.info("нет изменений относительно main — PR не создаём")
        return result

    with repo.config_writer() as cw:
        cw.set_value("user", "name", "media-agents-bot")
        cw.set_value("user", "email", "bot@media-agents")
    repo.index.commit(f"spravochnik: {item['slug']} ({item['type']})")
    repo.remotes.origin.push(refspec=f"{result.branch}:{result.branch}", force=True)

    pr = _create_draft_pr(cfg, result.branch,
                          f"База знаний: {item['term']} ({item['type']})",
                          _pr_body(item, path))
    result.pr_url, result.pr_number = pr["url"], pr["number"]
    log.info("draft-PR создан: %s", result.pr_url)
    return result


def is_pr_merged(pr_number: int) -> bool:
    """Проверяет, смёржен ли PR (GitHub API). Для sync-шага очереди в run.py."""
    cfg = _git_env()
    resp = requests.get(
        f"{GITHUB_API}/repos/{cfg['repo']}/pulls/{pr_number}",
        headers={"Authorization": f"Bearer {cfg['token']}",
                 "Accept": "application/vnd.github+json"},
        timeout=30,
    )
    if resp.status_code >= 300:
        log.warning("не удалось прочитать PR #%s (%s)", pr_number, resp.status_code)
        return False
    return bool(resp.json().get("merged"))
