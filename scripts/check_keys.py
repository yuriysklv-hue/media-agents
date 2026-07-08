#!/usr/bin/env python3
"""Диагностика LLM-ключей: подбор рабочего endpoint и имён моделей для GLM,
проверка DeepSeek. Ничего не публикует — только сетевые пробы и отчёт.

Usage:
  # ключ можно передать через окружение или .env (ZHIPU_API_KEY, DEEPSEEK_API_KEY)
  ZHIPU_API_KEY=... python scripts/check_keys.py
  python scripts/check_keys.py            # если ключи уже в .env
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

try:
    from openai import OpenAI
except ImportError:
    sys.exit("Нет пакета openai — установите: pip install -r requirements.txt")

# Кандидаты для GLM: (метка, base_url). Первый рабочий фиксируем.
GLM_ENDPOINTS = [
    ("z.ai", "https://api.z.ai/api/paas/v4"),
    ("bigmodel", "https://open.bigmodel.cn/api/paas/v4"),
]
GLM_FLASH_CANDIDATES = ["glm-4.5-flash", "glm-4-flash", "glm-4.6-flash", "glm-4.5-air"]
GLM_EMBED_CANDIDATES = ["embedding-3", "embedding-2"]


def _chat_ok(url: str, key: str, model: str) -> tuple[bool, str]:
    try:
        client = OpenAI(base_url=url, api_key=key, timeout=30)
        r = client.chat.completions.create(
            model=model, max_tokens=16, temperature=0,
            messages=[{"role": "user", "content": "Ответь одним словом: работает?"}],
        )
        return True, (r.choices[0].message.content or "").strip()
    except Exception as exc:
        return False, str(exc).splitlines()[0][:120]


def _embed_ok(url: str, key: str, model: str) -> tuple[bool, str]:
    try:
        client = OpenAI(base_url=url, api_key=key, timeout=30)
        r = client.embeddings.create(model=model, input="test")
        return True, f"dim={len(r.data[0].embedding)}"
    except Exception as exc:
        return False, str(exc).splitlines()[0][:120]


def check_glm() -> None:
    key = os.environ.get("ZHIPU_API_KEY", "").strip()
    print("=== GLM / Zhipu ===")
    if not key:
        print("  ZHIPU_API_KEY не задан — пропуск\n")
        return

    # Если endpoint зафиксирован в env — проверяем только его.
    forced = os.environ.get("ZHIPU_BASE_URL", "").strip()
    endpoints = [("env", forced)] if forced else GLM_ENDPOINTS

    flash_candidates = (
        [os.environ["GLM_FLASH_MODEL"]] if os.environ.get("GLM_FLASH_MODEL")
        else GLM_FLASH_CANDIDATES
    )
    embed_candidates = (
        [os.environ["GLM_EMBEDDING_MODEL"]] if os.environ.get("GLM_EMBEDDING_MODEL")
        else GLM_EMBED_CANDIDATES
    )

    working_url = working_flash = working_embed = None
    for label, url in endpoints:
        for model in flash_candidates:
            ok, info = _chat_ok(url, key, model)
            mark = "OK " if ok else "-- "
            print(f"  {mark} chat  [{label:8}] {model:16} {info}")
            if ok and working_url is None:
                working_url, working_flash = url, model
        if working_url == url:
            for model in embed_candidates:
                ok, info = _embed_ok(url, key, model)
                mark = "OK " if ok else "-- "
                print(f"  {mark} embed [{label:8}] {model:16} {info}")
                if ok and working_embed is None:
                    working_embed = model
            break  # endpoint найден — второй не проверяем

    print()
    if working_url:
        print("  ✅ Рабочая конфигурация для .env:")
        print(f"     ZHIPU_BASE_URL={working_url}")
        print(f"     GLM_FLASH_MODEL={working_flash}")
        if working_embed:
            print(f"     GLM_EMBEDDING_MODEL={working_embed}")
        else:
            print("     # эмбеддинги не отвечают — дедуп деградирует до URL-дедупа (это ок)")
    else:
        print("  ❌ Ни один endpoint/модель не ответили. Проверьте ключ и доступность хоста.")
    print()


def check_deepseek() -> None:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    print("=== DeepSeek ===")
    if not key:
        print("  DEEPSEEK_API_KEY не задан — пропуск\n")
        return
    url = os.environ.get("DEEPSEEK_BASE_URL", "").strip() or "https://api.deepseek.com/v1"
    ok, info = _chat_ok(url, key, "deepseek-chat")
    print(f"  {'OK ' if ok else '-- '} chat  deepseek-chat  {info}\n")


if __name__ == "__main__":
    check_glm()
    check_deepseek()
