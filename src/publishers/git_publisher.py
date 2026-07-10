"""Publisher: ветка + PR в репозиторий media с готовыми .md файлами.

Сайт живёт в подпапке media-site/ внутри репо media — файлы кладутся в
{MEDIA_SITE_SUBDIR}/src/content/{news|digest}/{slug}.md. PR создаётся через
GitHub REST API (requests + GH_TOKEN) — gh CLI не требуется.
В DRY_RUN git-операции пропускаются, файлы остаются в drafts/.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..utils.config import ROOT, env_flag
from ..utils.frontmatter import split_front_matter
from ..utils.logger import get_logger
from ..utils.state import StateManager, read_jsonl, utcnow_iso

log = get_logger("publisher")

CLONE_DIR = ROOT / "media-clone"
GITHUB_API = "https://api.github.com"


@dataclass
class PublishItem:
    path: Path                      # черновик {slug}.md
    article_type: str = "news"      # news | digest
    event: dict | None = None       # curated_item (для embedding и реестра)


@dataclass
class PublishResult:
    published: int = 0
    pr_url: str | None = None
    branch: str | None = None
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)


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


def _create_pr(cfg: dict, branch: str, title: str, body: str) -> str:
    resp = requests.post(
        f"{GITHUB_API}/repos/{cfg['repo']}/pulls",
        headers={
            "Authorization": f"Bearer {cfg['token']}",
            "Accept": "application/vnd.github+json",
        },
        json={"title": title, "head": branch, "base": "main", "body": body},
        timeout=30,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"PR не создан ({resp.status_code}): {resp.text[:300]}")
    return resp.json()["html_url"]


def _pr_body(items: list[PublishItem], metrics: dict) -> str:
    rows = []
    for n, item in enumerate(items, 1):
        meta, _ = split_front_matter(item.path.read_text(encoding="utf-8"))
        source = (meta.get("source") or {}).get("title", "—")
        rows.append(f"| {n} | {meta.get('title', '')} | {item.path.stem} | {source} | PASS |")
    table = "\n".join(rows)
    m = {k: metrics.get(k, "—") for k in
         ("raw_items", "passed_pre_filter", "translated", "curated", "qa_failed",
          "llm_cost_usd", "duration")}
    return f"""## Новости — batch {datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}

{len(items)} статей прошло полный пайплайн.

### Статьи

| # | Заголовок | Slug | Источник | QA |
|---|-----------|------|----------|----|
{table}

### Метрики

- Собрано raw_items: {m['raw_items']}
- Прошло pre-filter: {m['passed_pre_filter']}
- Переведено: {m['translated']}
- Прошло filter+dedup: {m['curated']}
- QA FAIL: {m['qa_failed']}
- LLM cost: ${m['llm_cost_usd']}
- Время выполнения: {m['duration']}
"""


def _item_cost(state: StateManager, item_id: str | None) -> float:
    if not item_id:
        return 0.0
    usage = read_jsonl(state.state_dir / "llm_usage.jsonl")
    return round(sum(u.get("cost_usd", 0) for u in usage if u.get("item_id") == item_id), 4)


def _record_published(state: StateManager, item: PublishItem, pr_url: str | None) -> None:
    meta, _ = split_front_matter(item.path.read_text(encoding="utf-8"))
    event = item.event or {}
    primary = next((s for s in event.get("sources", []) if s.get("is_primary")), {})
    state.log_published({
        "slug": item.path.stem,
        "type": item.article_type,
        "title": meta.get("title", ""),
        "pub_date": str(meta.get("pubDate", "")),
        "category": meta.get("category", ""),
        "source_url": primary.get("source_url") or (meta.get("source") or {}).get("url", ""),
        "source_name": primary.get("source_name") or (meta.get("source") or {}).get("title", ""),
        "source_published_at": primary.get("published_at"),  # дата в источнике (pubDate теперь = выход на 1screen)
        "pr_url": pr_url,
        "pr_merged": False,
        "published_at": utcnow_iso(),
        "event_id": event.get("event_id"),
        "llm_cost_usd": _item_cost(state, event.get("event_id")),
        "qa_status": "PASS",
    })
    if event.get("embedding"):
        state.add_published_embeddings([event["embedding"]], [item.path.stem])


def publish(items: list[PublishItem], state: StateManager,
            metrics: dict | None = None, branch: str | None = None,
            pr_title: str | None = None) -> PublishResult:
    """Кладёт QA-PASS файлы в клон media, пушит ветку, открывает PR."""
    result = PublishResult()
    if not items:
        log.info("публиковать нечего — пустой PR не создаём")
        return result
    metrics = metrics or {}

    if env_flag("DRY_RUN"):
        result.dry_run = True
        log.info("DRY_RUN: git-операции пропущены, %d файлов остаются в drafts/", len(items))
        return result

    cfg = _git_env()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    branch = branch or f"news-batch-{stamp}"
    result.branch = branch

    repo = _prepare_clone(cfg["url"])
    repo.git.checkout("-B", branch)

    for item in items:
        target_dir = CLONE_DIR / cfg["subdir"] / "src" / "content" / item.article_type
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.path, target_dir / item.path.name)

    repo.git.add(A=True)
    if not repo.is_dirty(untracked_files=True):
        log.info("нет изменений относительно main — PR не создаём")
        return result

    with repo.config_writer() as cw:
        cw.set_value("user", "name", "media-agents-bot")
        cw.set_value("user", "email", "bot@media-agents")
    label = "digest" if items[0].article_type == "digest" else "news"
    repo.index.commit(f"{label}: add {len(items)} article(s) [batch {stamp}]")
    repo.remotes.origin.push(refspec=f"{branch}:{branch}", force=True)

    title = pr_title or f"Новости от агентов — batch {stamp} ({len(items)} шт.)"
    result.pr_url = _create_pr(cfg, branch, title, _pr_body(items, metrics))
    log.info("PR создан: %s", result.pr_url)

    for item in items:
        _record_published(state, item, result.pr_url)
        item.path.unlink()  # черновик уходит из drafts/ после публикации
    result.published = len(items)
    return result
