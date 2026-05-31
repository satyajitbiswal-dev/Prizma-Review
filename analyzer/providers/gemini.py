import time
import logging
import requests
from django.conf import settings
from analyzer.providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)

GEMINI_BASE_URL  = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL    = "gemini-2.0-flash"

# Force Gemini to return structured JSON matching your schema
RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "line_start":        {"type": "integer"},
            "line_end":          {"type": "integer"},
            "severity":          {"type": "string", "enum": ["CRITICAL", "WARNING", "SUGGESTION"]},
            "category":          {"type": "string"},
            "issue":             {"type": "string"},
            "suggestion":        {"type": "string"},
            "complexity_before": {"type": "string"},
            "complexity_after":  {"type": "string"},
        },
        "required": ["line_start", "line_end", "severity", "issue", "suggestion"],
    },
}


class GeminiAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"Gemini HTTP {status_code}: {message}")


class GeminiProvider(BaseLLMProvider):

    def __init__(self, api_key: str = None, model: str = None):
        # Accept key directly (from rotator) OR fall back to settings
        self.api_key = api_key or getattr(settings, "GEMINI_API_KEY", "").strip()
        self.model   = model or getattr(settings, "GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

        if not self.api_key:
            raise GeminiAPIError(401, "No Gemini API key provided")

    def _resolve_key(self) -> str:
        key = getattr(settings, "GEMINI_API_KEY", "").strip()
        if not key:
            raise GeminiAPIError(401, "GEMINI_API_KEY missing from .env")
        return key

    def call_model(self, system_prompt: str, user_prompt: str,
                   retries: int = 3) -> str:

        # Always build URL cleanly — never double "models/"
        model_path = self.model if self.model.startswith("models/") else f"models/{self.model}"
        url = f"{GEMINI_BASE_URL.rstrip('/')}/{model_path.lstrip('models/')}:generateContent"

        headers = {
            "Content-Type":  "application/json",
            "X-Goog-Api-Key": self.api_key,
        }

        body = {
            "system_instruction": {
                "parts": [{"text": system_prompt}]
            },
            "contents": [
                {"role": "user", "parts": [{"text": user_prompt}]}
            ],
            "generationConfig": {
                "temperature":       0.1,
                "maxOutputTokens":   4096,
                "responseMimeType":  "application/json",
                "responseSchema":    RESPONSE_SCHEMA,
            },
        }

        last_exc = None
        for attempt in range(retries):
            try:
                resp = requests.post(url, json=body, headers=headers, timeout=30)

                if resp.status_code >= 400:
                    msg = self._parse_error(resp)
                    logger.error("Gemini %s (attempt %s): %s",
                                 resp.status_code, attempt + 1, msg)
                    exc = GeminiAPIError(resp.status_code, msg)

                    # Don't retry auth/bad-request errors
                    if resp.status_code in (400, 401, 403):
                        raise exc

                    last_exc = exc
                    time.sleep(2 ** attempt)
                    continue

                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]

            except GeminiAPIError:
                raise
            except (KeyError, IndexError) as e:
                logger.error("Unexpected Gemini response structure: %s", e)
                raise GeminiAPIError(500, f"Unexpected response: {e}")
            except requests.RequestException as e:
                logger.error("Gemini network error (attempt %s): %s", attempt + 1, e)
                last_exc = e
                time.sleep(2 ** attempt)

        raise last_exc or GeminiAPIError(500, "Gemini failed after retries")

    def _parse_error(self, resp: requests.Response) -> str:
        try:
            err = resp.json().get("error", {})
            return err.get("message", resp.text[:300]) if isinstance(err, dict) else resp.text[:300]
        except ValueError:
            return resp.text[:300]