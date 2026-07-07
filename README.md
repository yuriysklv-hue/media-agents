# media-agents

Новостной пайплайн для медиа [1screen.ru](https://1screen.ru): сбор западных AdTech-новостей из RSS, фильтрация, перевод, **осмысленный рерайт** на русском и публикация через PR в репозиторий сайта (`yuriysklv-hue/media`, подпапка `media-site/`).

Спецификация: `для_кодинга/ТЗ_Новостной_пайплайн.md`. Полная архитектура: `для_кодинга/reference/03_Архитектура_агентов.md`.

## Конвейер

```
RSS (5 фидов) → Pre-Filter (keywords + GLM) → Translator (DeepSeek)
  → Filter+Dedup (embeddings) → Author News (DeepSeek, рерайт)
  → Enricher (GLM) → QA (rules + GLM) → Publisher (PR в media)
```

Перевод — рабочий материал для извлечения фактов; Author News пишет оригинальный текст. Все материалы идут через PR с ручным ревью.

## Установка

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполнить ключи
```

Ключи в `.env`:

| Переменная | Где взять |
|---|---|
| `DEEPSEEK_API_KEY` | [platform.deepseek.com](https://platform.deepseek.com/) |
| `ZHIPU_API_KEY` | [open.bigmodel.cn](https://open.bigmodel.cn) → API Keys (GLM-4-Flash бесплатен) |
| `GH_TOKEN` | GitHub PAT с правами `repo` + `pull-requests` на репо `media` |
| `MEDIA_REPO` | `yuriysklv-hue/media` |
| `MEDIA_SITE_SUBDIR` | `media-site` |

Без `ZHIPU_API_KEY` пайплайн работает в деградированном режиме (фильтрация только по ключевым словам, enricher-фолбэк, QA только rules-based). Без `DEEPSEEK_API_KEY` пайплайн останавливается — перевод и написание невозможны.

## Запуск

```bash
python run_pipeline.py                    # полный цикл: сбор → ... → PR
python run_pipeline.py --collect-only     # только сбор RSS (без LLM)
python run_pipeline.py --skip-collect     # обработка существующих raw_items
python run_pipeline.py --dry-run          # без git-операций, файлы в data/drafts/
python run_pipeline.py --stage translator # один этап

python run_digest.py                      # дайджест текущей недели
python run_digest.py --week 2026-W27

python scripts/cost_report.py             # расходы LLM за 7 дней
pytest                                    # тесты (scoring, slug, qa — без API)
```

## Данные

```
data/
├── inbox/        # raw → passed_pre_filter → translated → curated (gitignored)
├── drafts/       # news/, digest/, failed/ — черновики до публикации (gitignored)
├── state/        # seen_urls, кэш переводов, llm_usage.jsonl (КОММИТИТСЯ)
├── published/    # published.jsonl + embeddings.npz (КОММИТИТСЯ)
└── logs/         # runs.log (gitignored)
```

`data/state/` и `data/published/` коммитятся workflow'ами после каждого прогона — так дедупликация и кэш переводов переживают перезапуски GitHub Actions.

## GitHub Actions

- `news-pipeline.yml` — 09:00 и 21:00 МСК ежедневно + ручной запуск;
- `digest.yml` — воскресенье 21:00 МСК.

Secrets: `DEEPSEEK_API_KEY`, `ZHIPU_API_KEY`, `GH_TOKEN`, `MEDIA_REPO`, `MEDIA_SITE_SUBDIR`.

## Отличия от ТЗ (осознанные)

- **PR создаётся через GitHub REST API** (requests + `GH_TOKEN`), а не `gh` CLI — меньше внешних зависимостей, одинаково работает локально и на Actions.
- **`data/state/` и `data/published/` не в .gitignore** — ТЗ предлагало игнорировать всё `data/`, но workflow коммитит состояние; иначе `git add data/state/` был бы no-op и дедупликация не переживала бы перезапуски.
- **ID моделей Anthropic обновлены** (`claude-sonnet-5`, `claude-opus-4-8`) — в ТЗ были устаревшие датированные ID. Эскалация в MVP всё равно не активируется.
- **`MEDIA_GIT_URL` опционален** — собирается из `GH_TOKEN` + `MEDIA_REPO` автоматически.

## Контракт с сайтом

Категории (`config/vocabulary.yaml`) и авторы-службы (`config/authors.yaml`) зафиксированы в схеме сайта (`media-site/src/content.config.ts`, `src/lib/categories.ts`): категория — `z.enum`, авторы `news-world`/`news-ru`/`news-asia` созданы в коллекции `authors`. Новую категорию сначала добавить на сайте, потом в словарь.
