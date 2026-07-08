"""Универсальный клиент для LLM-вызовов.

Конфигурация провайдеров задаётся в config/models.yaml.
Все вызовы логируются в data/state/llm_usage.jsonl.
Все три провайдера (DeepSeek, GLM, Anthropic) — OpenAI-compatible API.
"""
from __future__ import annotations

import json
import os
import re

from openai import BadRequestError, NotFoundError, OpenAI
from tenacity import retry, retry_if_exception, retry_if_exception_type, stop_after_attempt, wait_exponential

from .utils.config import load_config
from .utils.logger import get_logger
from .utils.state import StateManager

log = get_logger("llm")

API_KEY_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "glm": "ZHIPU_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

# Переопределение endpoint без правки config/models.yaml — для смены платформы
# GLM (open.bigmodel.cn ↔ z.ai) или прокси DeepSeek.
BASE_URL_ENV = {
    "deepseek": "DEEPSEEK_BASE_URL",
    "glm": "ZHIPU_BASE_URL",
    "anthropic": "ANTHROPIC_BASE_URL",
}

# Прайс: $ за 1M токенов (input, output). Проверять перед запуском:
# DeepSeek — platform.deepseek.com, GLM — open.bigmodel.cn, Anthropic — platform.claude.com.
PRICING = {
    ("deepseek", "deepseek-chat"): (0.27, 1.10),
    ("deepseek", "deepseek-reasoner"): (0.55, 2.19),
    ("glm", "glm-4-flash"): (0.0, 0.0),
    ("glm", "embedding-3"): (0.0, 0.0),
    ("anthropic", "claude-sonnet-5"): (3.00, 15.00),
    ("anthropic", "claude-opus-4-8"): (5.00, 25.00),
}


class LLMUnavailable(RuntimeError):
    """Провайдер не сконфигурирован (нет API-ключа) или стабильно недоступен."""


class LLMClient:
    def __init__(self, provider: str, config: dict | None = None, state: StateManager | None = None):
        """
        provider: 'deepseek' | 'glm' | 'anthropic'
        config: загруженный config/models.yaml (по умолчанию читается сам)
        """
        self.provider = provider
        config = config or load_config("models")
        # env-override приоритетнее config — переключение платформы без правки кода.
        self.base_url = os.environ.get(BASE_URL_ENV[provider], "").strip() or config[provider]["base_url"]
        self.api_key = self._get_api_key(provider)
        self.client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        self.state = state or StateManager()

    @staticmethod
    def _get_api_key(provider: str) -> str:
        key = os.environ.get(API_KEY_ENV[provider], "").strip()
        if not key:
            raise LLMUnavailable(
                f"{API_KEY_ENV[provider]} не задан — провайдер {provider} недоступен"
            )
        return key

    @staticmethod
    def _calculate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        price_in, price_out = PRICING.get((provider, model), (0.0, 0.0))
        return input_tokens / 1e6 * price_in + output_tokens / 1e6 * price_out

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _chat_once(self, model: str, messages: list[dict], temperature: float,
                   max_tokens: int, response_format: dict | None):
        kwargs: dict = dict(model=model, messages=messages, max_tokens=max_tokens)
        if temperature is not None:
            kwargs["temperature"] = temperature
        if response_format:
            kwargs["response_format"] = response_format
        return self.client.chat.completions.create(**kwargs)

    def chat(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        response_format: dict | None = None,
        stage: str = "unknown",
        item_id: str | None = None,
    ) -> str:
        """Синхронный chat completion. Возвращает текст ответа; логирует usage."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        response = self._chat_once(model, messages, temperature, max_tokens, response_format)
        usage = response.usage
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        self.state.log_llm_usage(
            stage=stage,
            provider=self.provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=self._calculate_cost(self.provider, model, input_tokens, output_tokens),
            item_id=item_id,
        )
        return response.choices[0].message.content or ""

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=30),
        # 400/404 (неизвестная модель, платформа без эмбеддингов) — не транзиентны,
        # повторять бессмысленно; ретраим только сетевые/5xx/429.
        retry=retry_if_exception(lambda e: not isinstance(e, (BadRequestError, NotFoundError))),
        reraise=True,
    )
    def embed(self, model: str, text: str, stage: str = "embedding",
              item_id: str | None = None) -> list[float]:
        """Embedding-вектор. На платформах без эмбеддингов (z.ai) вызов падает
        с 400 — дедуп деградирует до URL-дедупа (см. filter_dedup)."""
        # embedding-3 принимает до ~3072 токенов — обрезаем длинные тексты.
        response = self.client.embeddings.create(model=model, input=text[:6000])
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        self.state.log_llm_usage(
            stage=stage,
            provider=self.provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=0,
            cost_usd=self._calculate_cost(self.provider, model, input_tokens, 0),
            item_id=item_id,
        )
        return response.data[0].embedding


# Переопределение имён моделей без правки config/models.yaml (напр. для z.ai,
# где flash называется glm-4.5-flash). Формат env: <PROVIDER>_<ALIAS>_MODEL.
def _model_env(provider: str, alias: str) -> str | None:
    return os.environ.get(f"{provider.upper()}_{alias.upper()}_MODEL", "").strip() or None


def resolve_model(assignment: str, config: dict | None = None) -> tuple[str, str]:
    """'glm:flash' из pipeline-секции models.yaml → ('glm', 'glm-4-flash').

    Имя модели можно переопределить env-переменной, напр. GLM_FLASH_MODEL=glm-4.5-flash.
    """
    config = config or load_config("models")
    provider, alias = assignment.split(":", 1)
    model = _model_env(provider, alias) or config[provider]["models"][alias]
    return provider, model


def pipeline_client(stage: str, state: StateManager | None = None) -> tuple[LLMClient, str]:
    """Клиент + имя модели для этапа пайплайна (по config/models.yaml)."""
    config = load_config("models")
    provider, model = resolve_model(config["pipeline"][stage], config)
    return LLMClient(provider, config, state=state), model


_JSON_RE = re.compile(r"[\[{].*[\]}]", re.DOTALL)


def parse_json_response(text: str):
    """Достаёт JSON из ответа LLM (снимает ```-обёртки и преамбулы)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"\A```[a-zA-Z]*\s*\n?|\n?```\s*\Z", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_RE.search(text)
        if not m:
            raise
        return json.loads(m.group(0))
