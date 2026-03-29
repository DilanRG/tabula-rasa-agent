import asyncio
import logging
import os
from typing import Any, Dict, List, Optional
from openai import AsyncOpenAI, APIStatusError, APIConnectionError
import yaml

logger = logging.getLogger(__name__)

# HTTP status codes that indicate a model is unloaded or temporarily unavailable
# in LM Studio (400 = "Model unloaded", 503 = service unavailable).
_RETRYABLE_STATUS_CODES = {400, 503}
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0  # seconds; doubled on each successive attempt


class LLMManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.client = AsyncOpenAI(
            base_url=config['lm_studio']['host'] + "/v1",
            api_key=config['lm_studio']['api_key']
        )

    async def _call_once(self, model_type: str, messages: List[Dict[str, str]],
                         stream: bool, tools: Optional[List[Dict[str, Any]]]):
        """Build params and fire a single chat-completion request for *model_type*."""
        model_cfg = self.config['models'][model_type]
        model_name = model_cfg['name']

        params = {
            "model": model_name,
            "messages": messages,
            "temperature": self.config['sampling']['temperature'],
            "top_p": self.config['sampling']['top_p'],
            "max_tokens": self.config['sampling']['max_tokens'],
            "stream": stream,
            "extra_body": {
                "presence_penalty": self.config['sampling']['presence_penalty'],
                "frequency_penalty": 0.0,
            }
        }
        if tools:
            params["tools"] = tools

        return await self.client.chat.completions.create(**params)

    async def chat(self, messages: List[Dict[str, str]], model_type: str = "small",
                   stream: bool = False, tools: Optional[List[Dict[str, Any]]] = None):
        """
        Send a chat-completion request with retry logic and large->small fallback.

        Retries up to _MAX_RETRIES times on retryable HTTP errors (400 "Model
        unloaded", 503, or connection failures) using exponential backoff.
        If the requested model_type is "large" and all retries are exhausted,
        automatically falls back to the "small" model before giving up.
        """
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return await self._call_once(model_type, messages, stream, tools)
            except (APIStatusError, APIConnectionError) as exc:
                retryable = (
                    isinstance(exc, APIConnectionError)
                    or (isinstance(exc, APIStatusError)
                        and exc.status_code in _RETRYABLE_STATUS_CODES)
                )
                if not retryable:
                    raise

                last_exc = exc
                model_name = self.config['models'][model_type]['name']
                delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "LLM request failed (attempt %d/%d) for model '%s': %s — retrying in %.1fs",
                    attempt, _MAX_RETRIES, model_name, exc, delay,
                )
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(delay)

        # All retries exhausted — fall back to the small model if we were using large
        if model_type != "small":
            fallback_name = self.config['models']['small']['name']
            original_name = self.config['models'][model_type]['name']
            logger.warning(
                "Model '%s' unavailable after %d retries; falling back to small model '%s'.",
                original_name, _MAX_RETRIES, fallback_name,
            )
            try:
                return await self._call_once("small", messages, stream, tools)
            except Exception as fallback_exc:
                logger.error("Fallback to small model also failed: %s", fallback_exc)
                raise fallback_exc from last_exc

        raise last_exc


def load_config(path: str = "config.yaml") -> Dict[str, Any]:
    with open(path, 'r') as f:
        return yaml.safe_load(f)
