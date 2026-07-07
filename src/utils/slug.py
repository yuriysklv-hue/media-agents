"""Slug-генерация: транслит + kebab-case + уникальность."""
from __future__ import annotations

from slugify import slugify

# Устоявшиеся написания брендов в slug'ах (до транслитерации).
BRAND_REPLACEMENTS = [
    ("яндекс", "yandex"),
    ("вконтакте", "vk"),
    ("2гис", "2gis"),
]


def generate_slug(title: str, max_length: int = 60) -> str:
    """Транслит + kebab-case + обрезка по границе слова."""
    lowered = title.lower()
    for src, dst in BRAND_REPLACEMENTS:
        lowered = lowered.replace(src, dst)
    s = slugify(lowered, lowercase=True, separator="-")
    if len(s) > max_length:
        cut = s[:max_length]
        s = cut.rsplit("-", 1)[0] if "-" in cut else cut
    return s


def ensure_unique(slug: str, existing: set[str]) -> str:
    """Добавляет числовой суффикс, пока slug занят."""
    if slug not in existing:
        return slug
    n = 2
    while f"{slug}-{n}" in existing:
        n += 1
    return f"{slug}-{n}"
