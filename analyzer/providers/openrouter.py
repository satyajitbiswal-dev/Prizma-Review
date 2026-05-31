import time
import logging
import requests
from django.conf import settings
from analyzer.providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL  = "meta-llama/llama-3.3-70b-instruct"


class OpenRouterAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"OpenRouter HTTP {status_code}: {message}")


class OpenRouterProvider(BaseLLMProvider):

    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or getattr(settings, "LLM_API_KEY", "").strip()
        self.model   = model or getattr(settings, "LLM_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

        if not self.api_key:
            raise OpenRouterAPIError(401, "No OpenRouter API key provided")
        
    def _resolve_key(self) -> str:
        key = getattr(settings, "LLM_API_KEY", "").strip()
        if not key:
            raise OpenRouterAPIError(401, "LLM_API_KEY missing from .env")
        return key

    def call_model(self, system_prompt: str, user_prompt: str,
                   retries: int = 3) -> str:
        site_url = getattr(settings, "OPENROUTER_SITE_URL", "http://localhost:8000")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  site_url,
            "X-Title":       "PrizmReview",
        }
        body = {
            "model":       self.model,
            "temperature": 0.1,
            "max_tokens":  4096,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
        }

        last_exc = None
        for attempt in range(retries):
            try:
                resp = requests.post(OPENROUTER_URL, json=body,
                                     headers=headers, timeout=60)

                if resp.status_code >= 400:
                    msg = self._parse_error(resp)
                    logger.error("OpenRouter %s (attempt %s): %s",
                                 resp.status_code, attempt + 1, msg)
                    exc = OpenRouterAPIError(resp.status_code, msg)
                    if resp.status_code in (400, 401, 402, 403):
                        raise exc
                    last_exc = exc
                    time.sleep(2 ** attempt)
                    continue

                return resp.json()["choices"][0]["message"]["content"]

            except OpenRouterAPIError:
                raise
            except (KeyError, IndexError) as e:
                raise OpenRouterAPIError(500, f"Unexpected response: {e}")
            except requests.RequestException as e:
                logger.error("OpenRouter network error (attempt %s): %s", attempt + 1, e)
                last_exc = e
                time.sleep(2 ** attempt)

        raise last_exc or OpenRouterAPIError(500, "OpenRouter failed after retries")

    def _parse_error(self, resp: requests.Response) -> str:
        try:
            err = resp.json().get("error", {})
            return err.get("message", resp.text[:300]) if isinstance(err, dict) else resp.text[:300]
        except ValueError:
            return resp.text[:300]