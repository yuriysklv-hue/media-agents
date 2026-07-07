# SEO, GEO и AI-разметка

> Документ описывает правила структурирования контента и разметки страниц для поисковых систем, картографических сервисов и LLM-чатов. На основе этого документа реализуется SEO-компонент сайта (в Astro) и формируется системный промпт для SEO/geo-агента (см. `03_Архитектура_агентов.md`, раздел 3.6).

**Три слоя разметки:**

1. **SEO** — для классических поисковиков (Яндекс, Google).
2. **GEO** — географическая привязка контента (регионы, ссылки на карты, локальный SEO).
3. **AI-разметка** — для LLM-чатов (ChatGPT, Claude, Perplexity, Яндекс GPT, GigaChat) и ответных движков (Answer Engines).

***

## 1. Структура URL

### Принципы

* **Человекопонятные slug-и**, транслит или английский: `/news/yandex-native-format-rsy-2026`.
* **Без дат в URL** (после 2026 года датированные URL — устаревшая практика, усложняет updейты).
* **Без `.html`** и других расширений.
* **Иерархичные, но не глубже 3 уровней**: `/category/[slug]`, `/author/[slug]`, `/[article-slug]`.
* **Кириллица в URL** запрещена — Punycode или транслит.

### Карта URL

| URL                                                    | Тип страницы                  | Schema.org основной тип            |
| ------------------------------------------------------ | ----------------------------- | ---------------------------------- |
| `/`                                                    | Главная                       | `WebSite` + `ItemList`             |
| `/news`, `/digest`, `/reviews`, `/reports`, `/columns` | Разделы по типу контента      | `CollectionPage`                   |
| `/category/[slug]`                                     | Категория                     | `CollectionPage`                   |
| `/author/[slug]`                                       | Автор                         | `ProfilePage` + `Person`           |
| `/[article-slug]`                                      | Материал (news/review/digest) | `NewsArticle` или `Article`        |
| `/reports/[slug]`                                      | Отчёт/исследование            | `Article` + `Dataset` (для данных) |
| `/about`, `/advertise`, `/subscribe`                   | Статические                   | `WebPage`                          |

### Slug-генерация

* Алгоритм: `translit(title)` → `kebab-case` → обрезка до 60 символов → проверка уникальности.
* Для известных брендов —保留 brand-slug: `yandex`, `vk`, `alibaba`, `baidu`.
* При коллизии — добавляем числовой суффикс: `yandex-native-format-rsy-2026-2`.

***

## 2. Мета-теги (обязательные на каждой странице)

### Базовые

```html
<title>{Заголовок материала} | {Название медиа}</title>
<meta name="description" content="{Описание до 160 символов}">
<link rel="canonical" href="https://{domain}/{slug}">
<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1">
```

### Open Graph (для VK, Telegram, LinkedIn, Facebook)

```html
<meta property="og:type" content="article">
<meta property="og:title" content="{Заголовок}">
<meta property="og:description" content="{Описание}">
<meta property="og:url" content="https://{domain}/{slug}">
<meta property="og:image" content="https://{domain}/images/og/{slug}.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:site_name" content="{Название медиа}">
<meta property="og:locale" content="ru_RU">
<meta property="article:published_time" content="2026-07-10T08:00:00Z">
<meta property="article:modified_time" content="2026-07-10T12:00:00Z">
<meta property="article:author" content="https://{domain}/author/{slug}">
<meta property="article:section" content="{Категория}">
<meta property="article:tag" content="{Тег 1}">
<meta property="article:tag" content="{Тег 2}">
```

### Twitter Card

```html
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{Заголовок}">
<meta name="twitter:description" content="{Описание}">
<meta name="twitter:image" content="https://{domain}/images/og/{slug}.jpg">
<meta name="twitter:site" content="@{наш_аккаунт}">
<meta name="twitter:creator" content="@{аккаунт_автора}">
```

### Дополнительно

* `<meta name="theme-color">` для мобильных браузеров.
* `<link rel="alternate" type="application/rss+xml" title="..." href="/rss.xml">`.
* `<link rel="icon" type="image/svg+xml" href="/favicon.svg">`.

***

## 3. Schema.org разметка (JSON-LD)

### 3.1. Организация и сайт (на всех страницах, в `<head>`)

```json
{
  "@context": "https://schema.org",
  "@type": "NewsMediaOrganization",
  "@id": "https://{domain}/#organization",
  "name": "{Название медиа}",
  "url": "https://{domain}/",
  "logo": {
    "@type": "ImageObject",
    "url": "https://{domain}/logo.png",
    "width": 512,
    "height": 512
  },
  "sameAs": [
    "https://t.me/...",
    "https://vk.com/...",
    "https://twitter.com/...",
    "https://www.linkedin.com/company/..."
  ],
  "foundingDate": "2026-07",
  "areaServed": ["RU", "KZ", "BY", "UA"],
  "knowsLanguage": "ru"
}
```

```json
{
  "@context": "https://schema.org",
  "@type": "WebSite",
  "@id": "https://{domain}/#website",
  "url": "https://{domain}/",
  "name": "{Название медиа}",
  "publisher": { "@id": "https://{domain}/#organization" },
  "potentialAction": {
    "@type": "SearchAction",
    "target": "https://{domain}/search?q={query}",
    "query-input": "required name=query"
  }
}
```

### 3.2. Материал-новость (`NewsArticle`)

```json
{
  "@context": "https://schema.org",
  "@type": "NewsArticle",
  "headline": "{Заголовок до 110 символов}",
  "description": "{Описание}",
  "image": ["https://{domain}/images/og/{slug}.jpg"],
  "datePublished": "2026-07-10T08:00:00Z",
  "dateModified": "2026-07-10T12:00:00Z",
  "author": {
    "@type": "Person",
    "@id": "https://{domain}/author/{slug}#person",
    "name": "{Имя автора}"
  },
  "publisher": { "@id": "https://{domain}/#organization" },
  "mainEntityOfPage": {
    "@type": "WebPage",
    "@id": "https://{domain}/{slug}"
  },
  "articleSection": "{Категория}",
  "keywords": "через, запятую",
  "inLanguage": "ru",
  "isAccessibleForFree": true,
  "speakable": {
    "@type": "SpeakableSpecification",
    "cssSelector": [".article-lead", ".article-summary"]
  }
}
```

`Speakable` — важно для голосовых ассистентов и подкастов-сводок.

### 3.3. Аналитический отчёт (`Article` + `Dataset`)

Для Reports дополнительно публикуем метаданные о данных:

```json
{
  "@context": "https://schema.org",
  "@type": "Article",
  "headline": "...",
  "articleBody": "...",
  "author": {...},
  "about": {
    "@type": "Thing",
    "name": "Рынок рекламы в геосервисах России"
  },
  "isPartOf": {
    "@type": "PublicationIssue",
    "datePublished": "2026-07",
    "name": "Состояние рынка Q3 2026"
  }
}
```

Если в отчёте есть датасет (CSV/Excel):

```json
{
  "@context": "https://schema.org",
  "@type": "Dataset",
  "name": "Российские DSP 2026 — сравнение",
  "description": "Сравнение 18 DSP на 15 параметрах...",
  "creator": { "@id": ".../#organization" },
  "license": "https://creativecommons.org/licenses/by/4.0/",
  "isAccessibleForFree": false,
  "distribution": {
    "@type": "DataDownload",
    "encodingFormat": "text/csv",
    "contentUrl": "https://{domain}/data/dsp-russia-2026.csv"
  }
}
```

### 3.4. Автор (`Person`)

На странице автора `/author/[slug]`:

```json
{
  "@context": "https://schema.org",
  "@type": "Person",
  "@id": "https://{domain}/author/{slug}#person",
  "name": "Юрий Соколов",
  "jobTitle": "Основатель медиа",
  "worksFor": { "@id": "https://{domain}/#organization" },
  "image": "https://{domain}/images/authors/{slug}.jpg",
  "url": "https://{domain}/author/{slug}",
  "sameAs": ["https://linkedin.com/in/...", "https://t.me/..."],
  "knowsAbout": [
    "AdTech", "Геореклама", "Рекламные продукты",
    "Монетизация медиа", "2ГИС"
  ],
  "alumniOf": "...",
  "description": "20+ лет в рекламе и adtech..."
}
```

### 3.5. Хлебные крошки (`BreadcrumbList`)

На всех страницах кроме главной:

```json
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "itemListElement": [
    { "@type": "ListItem", "position": 1, "name": "Главная", "item": "https://{domain}/" },
    { "@type": "ListItem", "position": 2, "name": "Adtech Россия", "item": "https://{domain}/category/adtech-ru" },
    { "@type": "ListItem", "position": 3, "name": "Яндекс запустил...", "item": "https://{domain}/{slug}" }
  ]
}
```

### 3.6. FAQ (если в материале есть Q\&A блоки)

```json
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [{
    "@type": "Question",
    "name": "...",
    "acceptedAnswer": { "@type": "Answer", "text": "..." }
  }]
}
```

***

## 4. Sitemap.xml

### Структура

* Один индексный файл `sitemap.xml` со ссылками на подсайтмапы:
  * `sitemap-articles.xml` — все материалы (приоритет 0.8).
  * `sitemap-categories.xml` — категории (приоритет 0.6).
  * `sitemap-authors.xml` — авторы (приоритет 0.5).
  * `sitemap-static.xml` — статические страницы (приоритет 0.4).
* Генерация через `@astrojs/sitemap`.

### Правила

* `<lastmod>` обновляется при каждом изменении материала.
* `<changefreq>` — `daily` для главной, `weekly` для статей, `monthly` для статических.
* `<priority>` — см. выше.
* Все URL канонические, без query-параметров.
* Подключить в Яндекс.Вебмастере и Google Search Console.

***

## 5. Robots.txt

```
User-agent: *
Allow: /
Disallow: /admin/
Disallow: /draft/
Disallow: /*?utm_
Disallow: /*?preview=

# Sitemap
Sitemap: https://{domain}/sitemap.xml

# === AI-боты — см. политику в разделе 8 ===

# Крупные LLM-боты — пускаем (хотим быть процитированными в ответах)
User-agent: GPTBot
Allow: /

User-agent: OAI-SearchBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: Applebot-Extended
Allow: /

User-agent: anthropic-ai
Allow: /

User-agent: YandexGPTBot
Allow: /

# Боты-скрейперы без benefit — блокируем при выявлении (см. лог)
# User-agent: Bytespider
# Disallow: /
```

> Политика по AI-ботам — см. раздел 8, обновляется по мере мониторинга.

***

## 6. Георазметка (GEO)

### 6.1. Язык и регион

* `<html lang="ru">`.
* `<meta name="language" content="Russian">`.
* `<meta name="geo.region" content="RU">` (если контент про Россию).
* `<meta name="geo.placename" content="Москва">` (если есть привязка к городу).

### 6.2. hreflang — на старте

На старте сайт только русскоязычный. hreflang не нужен, но добавить самоссылающий тег:

```html
<link rel="alternate" hreflang="ru" href="https://{domain}/{slug}">
<link rel="alternate" hreflang="x-default" href="https://{domain}/{slug}">
```

При добавлении английской версии — расширяем массив hreflang.

### 6.3. Локальный SEO (для отчётов и кейсов с геопривязкой)

Если материал про конкретный регион — добавляем `Place` или `City`:

```json
{
  "@context": "https://schema.org",
  "@type": "NewsArticle",
  "...": "...",
  "contentLocation": {
    "@type": "Place",
    "name": "Москва",
    "address": {
      "@type": "PostalAddress",
      "addressCountry": "RU"
    }
  }
}
```

### 6.4. Карты и геоссылки

* При упоминании компаний — ссылка на их представительство в Яндекс.Картах / 2ГИС (opportunistically, без over-spam).
* В разделах про георекламу — `geo` микроформат.

***

## 7. Версии для ИИ-чатов и Answer Engines

**Это критически важный раздел для 2026 года.** LLM-боты становятся новым каналом трафика — наравне с Google и Яндексом. Цель — чтобы ChatGPT, Claude, Perplexity, Яндекс GPT, GigaChat и другие чаще **цитировали наше медиа** в ответах на запросы про рекламу и маркетинг.

### 7.1. llms.txt — стандарт для LLM-читалки

Файл `/llms.txt` в корне сайта (markdown-формат, описан на [llmstxt.org](https://llmstxt.org)):

```markdown
# {Название медиа}

> Профессиональное медиа о рекламе, рекламных технологиях и маркетинге для России и СНГ.

## О нас
{Краткое описание — 2-3 предложения, что мы и для кого}

## Главные разделы
- [Новости](https://{domain}/news): свежие новости рекламного рынка
- [Отчёты](https://{domain}/reports): аналитические исследования
- [Обзоры инструментов](https://{domain}/reviews)
- [Мнения](https://{domain}/columns)

## Ключевые материалы (стартовая точка для LLM)
- [Реклама в геосервисах 2026](https://{domain}/reports/geo-ads-2026)
- [Состояние российского adtech Q3 2026](https://{domain}/reports/adtech-ru-q3-2026)
- [Все DSP России: сравнение](https://{domain}/reports/dsp-russia-compare)

## Контакты и правила цитирования
- Контакт: {email}
- При цитировании указывать источник и дату.
- Полный текст: см. llms-full.txt
```

### 7.2. llms-full.txt — расширенная версия

Полная выжимка всего контента в одном файле (генерируется автоматически при сборке). Включает:

* Все опубликованные материалы за последние 90 дней (title + lead + URL).
* Структуру рубрик.
* Описания авторов.
* Обновляется при каждом деплое.

### 7.3. Специальная разметка для LLM

**Каждый материал должен содержать в HTML:**

```html
<article itemscope itemtype="https://schema.org/NewsArticle">
  <h1 itemprop="headline">...</h1>
  <meta itemprop="datePublished" content="2026-07-10T08:00:00Z">
  <div itemprop="articleBody">
    <p class="article-summary">{Краткая выжимка 1-2 предложения — для LLM-ответов}</p>
    {Тело статьи}
  </div>
</article>
```

**Принципы LLM-friendly контента:**

* **Чёткая структура заголовков** H1 → H2 → H3, без пропусков уровней.
* **Первый абзац \= саммари** (LLM часто берёт его для ответа).
* **Маркированные списки и таблицы** — LLM их любят.
* **Короткие абзацы** по 2-4 предложения.
* **Явные цифры и факты** с указанием источника.
* **TL;DR в конце длинных материалов**.
* **Минимум water и общих фраз** (см. принцип «анти-ИИ-голос» в `00_Позиционирование`, раздел 7).

### 7.4. Стратегия по AI-ботам

| Бот                         | Принадлежит                   | Политика     | Обоснование                            |
| --------------------------- | ----------------------------- | ------------ | -------------------------------------- |
| `GPTBot`                    | OpenAI / ChatGPT              | **Allow**    | Хотим быть в ответах ChatGPT           |
| `OAI-SearchBot`             | OpenAI Search                 | **Allow**    | Поисковая выдача OpenAI                |
| `ChatGPT-User`              | Конечные пользователи ChatGPT | **Allow**    | Пользовательские запросы               |
| `ClaudeBot`, `anthropic-ai` | Anthropic / Claude            | **Allow**    | Хотим быть в ответах Claude            |
| `PerplexityBot`             | Perplexity                    | **Allow**    | Answer engine с цитированием           |
| `Google-Extended`           | Google Gemini                 | **Allow**    | Хотим быть в ответах Gemini            |
| `Applebot-Extended`         | Apple Intelligence            | **Allow**    | Apple Intelligence summaries           |
| `YandexGPTBot`              | Яндекс                        | **Allow**    | Российский рынок                       |
| `CCBot`                     | Common Crawl                  | **Allow**    | Датасеты для open-source LLM           |
| `Bytespider`                | ByteDance                     | **Monitor**  | Крупный игрок, но нет встречной выгоды |
| `Diffbot`, `ImagesiftBot`   | Extraction services           | **Disallow** | Чистый скрейпинг                       |

**Принцип:** пускаем ботов тех LLM, которые **ссылаются обратно** на источник (ChatGPT, Claude, Perplexity, Gemini). Блокируем чистых экстракторов без обратной пользы.

### 7.5. TDMRep (Text and Data Mining Reservation Protocol)

Если часть контента платная или под ограничениями — указываем через TDMRep в `/.well-known/tdmrep.json`:

```json
{
  "metadata": {
    "http://example.com/tdmrep/manifest": {
      "tdm:policies": [{
        "tdm:policy": "http://example.com/tdmrep/policy/en",
        "tdm:reservation": {
          "tdm:reserved": ["text-mining", "data-mining"],
          "tdm:targets": ["/premium/*"]
        }
      }]
    }
  }
}
```

### 7.6. AI-ATTRIBUTION (опционально, Релиз 3)

Когда будут b2b-сервисы, добавим метаданные для принудительной атрибуции при использовании контента в AI.

***

## 8. Канонические URL и дубли

* Каждая страница имеет `<link rel="canonical">` на себя.
* Если материал cross-posted (например, на VC.ru или Хабр) — `canonical` указывает на наш сайт.
* Пагинация: каждая страница пагинации имеет канонический URL (не指向 первой), но с `meta name="robots" content="noindex,follow"` для страниц 2+.
* Фильтры и сортировки — `noindex,follow`.

***

## 9. Производительность как SEO-фактор

* Core Web Vitals (Google): LCP \< 2.5s, INP \< 200ms, CLS \< 0.1.
* Яндекс.ИКС (индекс качества сайта) — зависит от контента, ссылок, поведенческих.
* Astro даёт LCP \< 1s по умолчанию (статика, нулевой JS).
* Изображения — WebP/AVIF, lazy-loading, `width`/`height` атрибуты.
* Шрифты — `font-display: swap`, preload.

***

## 10. Аналитика и мониторинг

### Инструменты

* **Яндекс.Вебмастер** — основной для RU. Следим за ИКС, индексацией, ошибками.
* **Google Search Console** — для глобальной индексации.
* **Yandex Metrika** — основная аналитика для RU-аудитории.
* **Plausible / Umami** — privacy-friendly аналитика, без cookie-баннеров.

### Метрики SEO

* Доля страниц в индексе.
* CTR из поиска (по queries).
* Средняя позиция по целевым запросам.
* Доля трафика из поиска (target: 40-60% на старте, рост со временем).

### Метрики AI-цитируемости (замеряем в Релиз 3)

* Реферреры от `chatgpt.com`, `perplexity.ai`, `claude.ai`, `you.com`.
* Упоминания бренда в AI-ответах (через регулярные прогоны тестовых запросов).
* Backlinks от AI-summary сайтов.

***

## 11. Чек-лист для каждой публикации

Перед публикацией любого материала:

* [ ] URL slug уникальный и человекопонятный.
* [ ] `<title>` 50-70 символов, включает ключевое слово.
* [ ] `<meta description>` 140-160 символов, с призывом или интригой.
* [ ] Canonical указан.
* [ ] OG-изображение 1200×630 px.
* [ ] Twitter Card настроен.
* [ ] JSON-LD NewsArticle валиден (через [Rich Results Test](https://search.google.com/test/rich-results)).
* [ ] BreadcrumbList сгенерирован.
* [ ] Хедеры H1-H3 без пропусков уровней.
* [ ] Все изображения с alt-текстом.
* [ ] Внутренние ссылки на 2-3 связанных материала.
* [ ] Проверка на дубль (через дедупликатор).
* [ ] Дата публикации в прошлом или настоящем (не будущая).
* [ ] Для отчётов: методология, источники данных, дата сбора.

***

## 12. Чек-лист при запуске сайта

* [ ] `robots.txt` загружен.
* [ ] `sitemap.xml` генерируется.
* [ ] `llms.txt` и `llms-full.txt` созданы.
* [ ] Все URL работают (нет 404 на ключевых страницах).
* [ ] 301 редиректы для типовых опечаток (`/news/` → `/news`).
* [ ] HTTPS включён, HSTS заголовок.
* [ ] Сайт добавлен в Яндекс.Вебмастер.
* [ ] Сайт добавлен в Google Search Console.
* [ ] Sitemap отправлен в обе панели.
* [ ] Yandex Metrika / Plausible установлены.
* [ ] Тестовый прогон через [PageSpeed Insights](https://pagespeed.web.dev) — score ≥ 90.
* [ ] Тест JSON-LD через Rich Results Test.
* [ ] Проверка через [schema.org Validator](https://validator.schema.org/).

***

## 13. Интеграция с работой SEO/geo-агента

Из документа `03_Архитектура_агентов.md` (раздел 3.6) — SEO/geo-агент (Enricher) отвечает за:

* Подбор оптимального slug.
* Генерацию description (160 символов).
* Подбор тегов из контролируемого словаря.
* Проверку соответствия чек-листа раздела 11.
* Категоризацию и гео-пометку.

**Системный промпт для SEO/geo-агента** формируется на основе этого документа и обновляется по мере накопления данных о ранжировании.