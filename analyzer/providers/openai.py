import time
import logging
import requests
from django.conf import settings
from analyzer.providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)

OPENAI_URL    = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"OpenAI HTTP {status_code}: {message}")


class OpenAIProvider(BaseLLMProvider):

    def __init__(self, api_key: str = None, model: str = None):
        """Pure Dependency Injection: Trusts the slot entry provided by the database rotator."""
        self.api_key = api_key.strip() if api_key else ""
        self.model   = model.strip() if model else "gpt-4o-mini"

        if not self.api_key:
            raise OpenAIAPIError(401, "No explicit API key supplied to OpenAI provider initialization.")

    def call_model(self, system_prompt: str, user_prompt: str,
                   retries: int = 3) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type":  "application/json",
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
                resp = requests.post(OPENAI_URL, json=body, headers=headers, timeout=30)

                if resp.status_code >= 400:
                    msg = self._parse_error(resp)
                    logger.error("OpenAI Endpoint Status %s (attempt %s): %s", resp.status_code, attempt + 1, msg)
                    
                    exc = OpenAIAPIError(resp.status_code, msg)
                    
                    # Intercept hard-failures instantly, including 402 financial constraints
                    if resp.status_code in (400, 401, 402, 403):
                        raise exc
                    
                    last_exc = exc
                    time.sleep(2 ** attempt)
                    continue

                return resp.json()["choices"][0]["message"]["content"]

            except OpenAIAPIError:
                raise
            except (KeyError, IndexError) as e:
                raise OpenAIAPIError(500, f"Malformed Provider API response content: {e}")
            except requests.RequestException as e:
                logger.error("OpenAI Transport/Network fault (attempt %s): %s", attempt + 1, e)
                last_exc = OpenAIAPIError(503, f"Network Transport Failure: {e}")
                time.sleep(2 ** attempt)

        raise last_exc or OpenAIAPIError(500, "OpenAI runtime processing pipeline exhausted all retry blocks.")

    def _parse_error(self, resp: requests.Response) -> str:
        try:
            err = resp.json().get("error", {})
            return err.get("message", resp.text[:300]) if isinstance(err, dict) else resp.text[:300]
        except ValueError:
            return resp.text[:300]