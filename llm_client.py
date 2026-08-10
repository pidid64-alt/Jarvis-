#!/usr/bin/env python3
"""
llm_client.py
-------------
Минимальный OpenAI-совместимый клиент к OmniRoute (localhost:20128/v1).

Используется только `requests` — никакого openai-sdk. Зависимости не
добавляются, протокол максимально простой, ошибки и таймауты обрабатываются.

Возвращает строку (контент ответа ассистента) или бросает LLMError —
jarvis.py ловит и падает на fallback.

Пример:
    client = LLMClient("http://localhost:20128/v1", api_key, "model-name")
    text = client.chat([{"role": "system", "content": "..."},
                        {"role": "user", "content": "..."}])
"""

import logging
import time
from typing import Iterable

import requests


class LLMError(Exception):
    """Любая ошибка LLM: сеть, таймаут, битый JSON, формат ответа."""


class LLMClient:
    def __init__(self, base_url: str, api_key: str | None,
                 model: str, timeout: float = 8.0, max_retries: int = 1):
        # base_url уже содержит /v1; chat completions добавляется сюда
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max(0, max_retries)

    def chat(self, messages: list[dict], max_tokens: int = 300,
             temperature: float = 0.2) -> str:
        """Возвращает текст первого choice. Бросает LLMError при любом сбое.

        temperature=0.2 — низкая, чтобы LLM не выдумывал команды и
        говорил в стиле парсера, а не писателя эссе.
        """
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            # Просим JSON-объект (OpenAI json_object mode). Не все провайдеры
            # это уважают — поэтому llm_parser всё равно ищет JSON в тексте.
            "response_format": {"type": "json_object"},
        }

        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                t0 = time.monotonic()
                resp = requests.post(
                    url, json=payload, headers=headers, timeout=self.timeout
                )
                latency = time.monotonic() - t0
                if resp.status_code != 200:
                    raise LLMError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                data = resp.json()
                choices = data.get("choices") or []
                if not choices:
                    raise LLMError("пустой choices в ответе")
                content = choices[0].get("message", {}).get("content", "")
                if not content:
                    raise LLMError("пустой content в первом choice")
                logging.info(
                    "LLM ok: latency=%.2fs, model=%s, tokens=%s",
                    latency, self.model,
                    data.get("usage", {}).get("total_tokens", "?")
                )
                return content
            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError) as e:
                last_err = e
                logging.warning("LLM network error (attempt %d): %s",
                                attempt + 1, e)
                # connection error — нет смысла ретраить много раз
                if attempt < self.max_retries:
                    time.sleep(0.3)
                    continue
            except LLMError as e:
                last_err = e
                logging.warning("LLM protocol error (attempt %d): %s",
                                attempt + 1, e)
                if attempt < self.max_retries:
                    time.sleep(0.3)
                    continue
            except Exception as e:
                # любой неожиданный сбой — оборачиваем, чтобы jarvis.py
                # мог единообразно падать в fallback
                raise LLMError(f"unexpected: {e}") from e

        raise LLMError(f"all attempts failed: {last_err}")