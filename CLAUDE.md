# media-agents — контекст для Claude

Пайплайн ИИ-агентов, наполняющий медиа **1screen.ru** новостями об adtech и маркетинге. Собирает англоязычные RSS, фильтрует, переводит и переписывает на русский, прогоняет через QA и **создаёт Pull Request в репозиторий `yuriysklv-hue/media`** (`media-site/src/content/news/`). На сайт материалы попадают только после ручного merge PR.

> Общий контекст проекта (бренд, позиционирование, дизайн сайта, деплой на Timeweb) — в `CLAUDE.md` репозитория **`media`**. Здесь — только про пайплайн. В новой сессии репозитории подключаются командой «добавь репозиторий media-agents» / «…media» (`add_repo`).

## Статус (09.07.2026)

- **Пайплайн в проде и работает.** Первый боевой батч — **9 статей — опубликован 09.07.2026** (PR #9 в `media` смёржен на сайт).
- Плановые прогоны по cron: новости **09:00 и 21:00 МСК**, дайджест вс 21:00 МСК. Идут с ветки **`main`** — фиксы должны попадать в `main`, иначе cron гоняет старый код.
- Первый прогон: 100 собрано → 21 pre-filter → 10 прошло QA → 9 опубликовано, ~14 мин, $0.08.

### Сессия обратной связи (09.07.2026, PR #5 смёржен)

Правки пайплайна по разбору первого батча владельцем:
- **Сноска о запрещённых в РФ организациях** — детерминированный пост-процессор `src/utils/legal.py` (подключён в `news_writer`): маркер `\*` у первого упоминания Meta/Facebook/Instagram + сноска в конце тела. WhatsApp исключён (принадлежит Meta, но не запрещён). Не полагаемся на дисциплину модели.
- **Description укладывается в 160 законченной фразой** — `enricher._fit_description` режет по границе предложения/слова, а не посреди слова (был обрыв «…необос…»). Промпт enricher требует цельной фразы.
- **Промпт `news_writer.md` усилен:** запрет обобщений-ярлыков («симптом растущего давления», «тренд») и шаблонных «выводных» концовок; политика цитат (спикер без авторитета для РФ — переформулировать или дать со ссылкой); гиперлинки на значимые цифры/исследования; экономный `**болд**` для акцентов (в рамках минимала сайта).
- **Текстовый фолбэк-дедуп** — `src/utils/text_similarity.py` + `filter_dedup._text_fallback_events`: раз эмбеддинги на z.ai выключены, дубли и склейка одной новости из разных фидов теперь ловятся по близости заголовков (token/префикс-Jaccard + SequenceMatcher). Раньше без эмбеддингов было по одному событию на item.
- Тесты: `tests/test_content_feedback.py`.

## Как устроен пайплайн

`run_pipeline.py` → этапы (`src/pipeline.py`): **collect → pre_filter → translator → filter_dedup → write → publish**

- **collect** (`src/collectors/rss_collector.py`) — RSS-фиды (Adweek, AdExchanger, Digiday, MediaPost…), ~100 сырых материалов.
- **pre_filter** (`src/processors/pre_filter.py`) — keyword-скоринг + GLM-скор релевантности.
- **translator** (`src/processors/translator.py`) — DeepSeek EN→RU (+ кэш переводов в `data/state/`).
- **filter_dedup** (`src/processors/filter_dedup.py`) — дедуп. Семантический (эмбеддинги) **отключён на z.ai**; работают URL-дедуп + **текстовый фолбэк по заголовкам** (`_text_fallback_events`, `src/utils/text_similarity.py`): отсев дублей опубликованного и склейка одной новости из разных фидов в одно событие (мультиисточник).
- **write** — DeepSeek пишет статью (`src/writers/news_writer.py`, после генерации `legal.add_restricted_org_footnotes` ставит сноску Meta/FB/Instagram); enricher (GLM) проставляет slug/description/category/geo/tags/author/social_title (`src/processors/enricher.py`, description укладывается в 160 законченной фразой); QA (GLM + детерминированные проверки, `src/processors/qa.py`) бракует слабые тексты в `data/drafts/failed/`.
- **publish** (`src/publishers/`) — коммит прошедших QA материалов в ветку репо `media` + создание PR.

## LLM-клиент — важные детали

`src/llm_client.py` — единый OpenAI-совместимый клиент. Провайдеры/модели в `config/models.yaml`, переопределяются env: `ZHIPU_BASE_URL`, `GLM_FLASH_MODEL`, `DEEPSEEK_BASE_URL` и т.п. (env приоритетнее config).

- **GLM** = `glm-4.5-flash` на **z.ai** (`https://api.z.ai/api/paas/v4`) — служебные JSON-этапы (pre_filter, enricher, qa, filter_dedup). **DeepSeek** = `deepseek-chat` — перевод и написание.
- ⚠️ **GLM thinking отключён принудительно.** `glm-4.5/4.6` на z.ai по умолчанию включают reasoning → тратят `max_tokens` на рассуждение и возвращают **пустой `content`**, из-за чего JSON-этапы падали на `json.loads("")` и уходили в фолбэк, а прогон упирался в тайм-аут. Клиент шлёт `extra_body={"thinking":{"type":"disabled"}}` для GLM-вызовов. **Не включать обратно** (аварийный откат — секрет `GLM_DISABLE_THINKING=0`).
- **Эмбеддинги z.ai не поддерживает** — вызов падает с 400, семантический дедуп деградирует до URL-дедупа (заложено, не баг).
- ⚠️ **Локально LLM протестировать нельзя** — ключи только в GitHub Secrets. Правки в LLM-логике проверяются прогоном воркфлоу (Actions → Run workflow), лог читается через MCP.

## Секреты (GitHub Actions) — все заданы

`ZHIPU_API_KEY`, `ZHIPU_BASE_URL` (`https://api.z.ai/api/paas/v4`), `GLM_FLASH_MODEL` (`glm-4.5-flash`), `DEEPSEEK_API_KEY`, `GH_TOKEN` (fine-grained PAT на репо `media`: **Contents R/W + Pull requests R/W**), `MEDIA_REPO` (`yuriysklv-hue/media`), `MEDIA_SITE_SUBDIR` (`media-site`). **`GLM_EMBEDDING_MODEL` НЕ задавать** (z.ai без эмбеддингов).

## Воркфлоу (`.github/workflows/`)

- `news-pipeline.yml` — основной (cron + `workflow_dispatch`), `timeout-minutes: 45`.
- `digest.yml` — недельный дайджест.
- `check-keys.yml` — диагностика LLM-ключей (кнопка Run workflow). **Всегда «зелёный» — смысл в тексте лога** (`OK chat` против `--`).
- ⚠️ Финальный шаг «Commit data state» делает `git pull --rebase origin main`; при запуске **с фиче-ветки** конфликтует (add/add) и красит job красным уже **после** создания PR. На `main` проходит чисто.

## Контракт с сайтом (репо `media`)

Схема материала — `media-site/src/content.config.ts`. Пайплайн обязан соблюдать (иначе сборка Astro на Timeweb падает и сайт не обновляется):

- `description ≤160` символов (`enricher.py` режет до 157+«…»), `social_title ≤100`.
- `category` — машинный слаг из `config/vocabulary.yaml` (`adtech-ru|adtech-world|adtech-asia|market-news|tools|creative`).
- `author` — служба-организация (`news-world`/`news-ru`/`news-asia`), должна существовать в `media-site/src/content/authors/`.
- `geo` ∈ `РФ|МИР|АЗИЯ`. Поля digest: `week` (`YYYY-Wnn`), `sources_count`.

## Команды (локальная отладка)

```bash
python scripts/check_keys.py        # диагностика ключей (endpoint/модели)
python run_pipeline.py --dry-run    # прогон без git, черновики в data/drafts/
python run_pipeline.py --stage collect   # один этап
python scripts/cost_report.py       # расходы LLM
pytest                              # тесты
```

## Открытые задачи и грабли

Актуальный список — в **`NEXT_SESSION.md`**. Ключевое:

- **Промпты / качество (главное):** QA стабильно бракует часть текстов как «ИИ-голос» (гладко, безлико, шаблонно). Основная цель докрутки — `src/prompts/news_writer.md`; сверять с `src/prompts/qa_style.md`.
- **Корпус good/bad:** забракованные QA черновики (`data/drafts/failed/`) живут на эфемерном раннере и теряются — сохранять как artifact/коммит с причиной QA, чтобы был размеченный материал для тюнинга.
- **`pubDate` = дата выхода на 1screen (UTC now)** — исправлено 10.07.2026 (`news_writer._finalize_meta`). Раньше ставилась дата источника из RSS → материалы датировались задним числом, выглядели несвежими и тонули в ленте. Дата источника теперь сохраняется отдельно в `published.jsonl` (`source_published_at`), во front-matter не идёт (контракт схемы сайта не расширяем).
- **Баги:** двоеточие в заголовке ломает YAML front-matter (`mapping values are not allowed here`); заголовки иногда вне 50–80 символов. Дедуп-дубли **частично закрыты** текстовым фолбэком по заголовкам (09.07.2026) — но это грубее эмбеддингов: ловит почти совпадающие заголовки, перефразировки одного сюжета из разных фидов может пропустить (нужен провайдер эмбеддингов или GLM-кластеризация).
- **Кэш Timeweb:** после merge PR сайт пересобирается, но свежие материалы видны не сразу (кэш/деплой). При «на сайте не видно» — сперва обновить кэш и проверить `/news`, а не искать баг в сборке.
