"""Тесты slug-генерации."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.slug import ensure_unique, generate_slug


def test_translit():
    """Русский → латиница → kebab-case."""
    slug = generate_slug("Google запускает новую функцию DSP")
    assert slug == "google-zapuskaet-novuiu-funktsiiu-dsp"


def test_max_length():
    """Обрезка до 60 символов по границе слова."""
    slug = generate_slug(
        "Очень длинный заголовок новости про программатик и ритейл-медиа на рынке рекламы"
    )
    assert len(slug) <= 60
    assert not slug.endswith("-")


def test_brand_names():
    """yandex, vk — устоявшееся написание, не транслит."""
    assert "yandex" in generate_slug("Яндекс обновил Директ")
    assert "vk" in generate_slug("ВКонтакте запустил новый формат")
    assert "google" in generate_slug("Google выкатил обновление")


def test_ensure_unique():
    existing = {"my-post", "my-post-2"}
    assert ensure_unique("my-post", existing) == "my-post-3"
    assert ensure_unique("fresh", existing) == "fresh"
