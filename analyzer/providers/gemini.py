import time
import random
import logging
import requests
from django.conf import settings
from analyzer.providers.base import BaseLLMProvider

logger = logging.getLogger(__name__)

GEMINI_BASE_URL  = "https://generativelanguage.googleapis.com/v1beta/models"
# Default fallback to Google's specialized open weight instruction variant
DEFAULT_MODEL    = "gemma-4-26b-a4b-it"

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
        self.api_key = api_key.strip() if api_key else ""
        self.model   = model.strip() if model else DEFAULT_MODEL

        if not self.api_key:
            raise GeminiAPIError(401, "No explicit API key supplied to Gemini provider initialization.")

    def call_model(self, system_prompt: str, user_prompt: str, retries: int = 3) -> str:
        # Standardize endpoint URI mapping structures
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

        base_delay = 1.5  
        last_exc = None

        for attempt in range(retries):
            try:
                resp = requests.post(url, json=body, headers=headers, timeout=30)

                if resp.status_code >= 400:
                    msg = self._parse_error(resp)
                    msg_lower = msg.lower()
                    
                    logger.error("Gemini API return code %s (attempt %s): %s", resp.status_code, attempt + 1, msg)
                    exc = GeminiAPIError(resp.status_code, msg)

                    # Hard error deflection
                    if resp.status_code in (400, 401, 403):
                        raise exc

                    # STEP 1: Auto-Recover Transient 503 Server Overload Bounds via Retry Backoff Loops
                    # Intercepts high-demand spikes on popular models like Gemma 26B without throwing errors upwards
                    if resp.status_code == 503 or "demand" in msg_lower:
                        if attempt == retries - 1:
                            raise exc
                        
                        # Apply randomized backoff math: base * 2^(attempt-1) + jitter decimals
                        jitter = random.uniform(0.1, 0.4)
                        sleep_duration = (base_delay * (2 ** attempt)) + jitter
                        
                        logger.warning(
                            f"Gemini cluster overloaded (503). Initiating Thread Backoff Jitter. "
                            f"Attempt {attempt+1}/{retries} sleeping for {sleep_duration:.2f}s"
                        )
                        time.sleep(sleep_duration)
                        continue

                    last_exc = exc
                    time.sleep(2 ** attempt)
                    continue

                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]

            except GeminiAPIError:
                raise
            except (KeyError, IndexError) as e:
                raise GeminiAPIError(500, f"Malformed Provider response structure layout: {e}")
            except requests.RequestException as e:
                logger.error("Gemini network transport layer error (attempt %s): %s", attempt + 1, e)
                last_exc = GeminiAPIError(503, f"Transport Exception: {e}")
                time.sleep(2 ** attempt)

        raise last_exc or GeminiAPIError(500, "Gemini processing engine exhausted all structural retries.")

    def _parse_error(self, resp: requests.Response) -> str:
        try:
            err = resp.json().get("error", {})
            return err.get("message", resp.text[:300]) if isinstance(err, dict) else resp.text[:300]
        except ValueError:
            return resp.text[:300]