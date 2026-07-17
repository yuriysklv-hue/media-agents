"""TF-IDF близость текстов — основной дедуп, когда эмбеддинги недоступны.

z.ai эмбеддинги не отдаёт (400), поэтому семантический дедуп через векторную
модель отключён. Здесь — детерминированная замена на scikit-learn: TF-IDF по
СИМВОЛЬНЫМ n-граммам (`char_wb`, 3–5), не по словам. Символьные граммы устойчивы
к русской словоизменительности («блокирует» ≈ «блокировку» ≈ «блокировка»
разделяют большинство 3–5-граммов) без внешнего стеммера/морфоанализатора —
это тот же принцип, что и префикс-стемминг в `text_similarity`, но плотнее и с
IDF-взвешиванием общих кусков.

Индекс пересобирается каждый прогон (корпус — десятки/сотни статей, fit+transform
дешевле поддержки инкрементального индекса). Кросс-язычность на этом этапе уже
снята переводом: у всех items есть `title_ru`/`summary_ru`, RU-фиды на русском,
мировые переведены — сравниваем русский с русским.
"""
from __future__ import annotations

# n-граммы символов: 3..5 — компромисс между чувствительностью к основам слов
# и устойчивостью к окончаниям. Меньше 3 — шум по буквам, больше 5 — теряется
# морфология. Параметры вынесены константами, чтобы докручивать по бою.
NGRAM_RANGE = (3, 5)
MIN_DF = 1  # корпус маленький — не выбрасываем редкие граммы


def _build_vectorizer():
    from sklearn.feature_extraction.text import TfidfVectorizer

    # sublinear_tf гасит вклад многократно повторённых граммов (длинные тексты),
    # strip_accents/lowercase нормализуют регистр. Стоп-слова не задаём: на уровне
    # символьных граммов они и так растворяются, а списка для русского в sklearn нет.
    return TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=NGRAM_RANGE,
        min_df=MIN_DF,
        lowercase=True,
        sublinear_tf=True,
    )


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def cross_similarity(queries: list[str], corpus: list[str]):
    """Матрица косинусной близости queries×corpus (len(queries) × len(corpus)).

    Векторизатор обучается на объединении обоих множеств — общий словарь граммов.
    Пустой corpus → матрица (len(queries) × 0). Пустые queries → (0 × …).
    """
    import numpy as np

    if not queries:
        return np.zeros((0, len(corpus)), dtype=np.float32)
    if not corpus:
        return np.zeros((len(queries), 0), dtype=np.float32)

    q = [_norm(t) for t in queries]
    c = [_norm(t) for t in corpus]
    vec = _build_vectorizer()
    matrix = vec.fit_transform(q + c)  # sparse, уже L2-нормирован
    qm, cm = matrix[: len(q)], matrix[len(q) :]
    return (qm @ cm.T).toarray().astype(np.float32)


def pairwise_similarity(texts: list[str]):
    """Симметричная матрица близости texts×texts (диагональ = 1)."""
    import numpy as np

    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    t = [_norm(x) for x in texts]
    vec = _build_vectorizer()
    matrix = vec.fit_transform(t)
    return (matrix @ matrix.T).toarray().astype(np.float32)
