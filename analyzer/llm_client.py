import json
import logging
import re
import time
import requests
from typing import List, Dict, Any, Optional
from django.conf import settings
from analyzer.prompts import DSA_SYSTEM_PROMPT, build_user_prompt
from analyzer.providers.gemini import GeminiProvider
from analyzer.providers.openai import OpenAIProvider

logger = logging.getLogger(__name__)

# ── Updated to OpenRouter Production Endpoint ─────────────────────────────
LLM_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct"


class OpenRouterAPIError(requests.HTTPError):
    """Raised when OpenRouter returns a non-2xx response (visible to Celery tasks)."""

    def __init__(self, status_code: int, message: str, response_body: str = ""):
        self.status_code = status_code
        self.openrouter_message = message
        self.response_body = response_body
        super().__init__(f"OpenRouter HTTP {status_code}: {message}")


def _resolve_openrouter_api_key() -> str:
    key = (getattr(settings, "LLM_API_KEY", None) or "").strip()
    if not key:
        raise OpenRouterAPIError(
            401,
            "LLM_API_KEY (or OPENROUTER_API_KEY) is missing. Set it in .env and restart the Celery worker.",
        )
    return key


def _resolve_openrouter_model() -> str:
    # getattr default does not apply when LLM_MODEL="" — that yields OpenRouter 400.
    model = (getattr(settings, "LLM_MODEL", None) or "").strip()
    if not model:
        model = DEFAULT_OPENROUTER_MODEL
        logger.warning("LLM_MODEL is empty; using default %s", model)
    return model


def _parse_openrouter_error(resp: requests.Response) -> str:
    try:
        payload = resp.json()
        err = payload.get("error") or {}
        if isinstance(err, dict):
            return err.get("message") or resp.text[:500]
        return str(err) or resp.text[:500]
    except ValueError:
        return resp.text[:500]


def call_llm(system_prompt: str, user_prompt: str, retries: int = 3) -> str:
    """Raw OpenRouter API call using unified bearer token headers."""
    api_key = _resolve_openrouter_api_key()
    model = _resolve_openrouter_model()
    site_url = getattr(settings, "OPENROUTER_SITE_URL", "http://localhost:8000")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": site_url,
        "X-OpenRouter-Title": "PrizmaReview",
    }

    body = {
        "model": model,
        "temperature": 0.1,
        "max_tokens": 4096,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    last_error: Optional[OpenRouterAPIError] = None

    for attempt in range(retries):
        try:
            resp = requests.post(LLM_URL, json=body, headers=headers, timeout=60)
            if resp.status_code >= 400:
                detail = _parse_openrouter_error(resp)
                logger.error(
                    "OpenRouter %s for model=%r: %s",
                    resp.status_code,
                    model,
                    detail,
                )
                last_error = OpenRouterAPIError(resp.status_code, detail, resp.text[:2000])
                # Do not retry client errors (400/401/402/403) — config won't fix itself.
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    raise last_error
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise last_error

            try:
                data = resp.json()
            except ValueError as e:
                logger.critical(
                    "OpenRouter returned non-JSON response: %s -- body: %s",
                    e,
                    resp.text[:500],
                )
                raise

            return data["choices"][0]["message"]["content"]

        except OpenRouterAPIError:
            raise
        except requests.exceptions.RequestException as e:
            logger.error(
                "OpenRouter network error (attempt %s/%s): %s",
                attempt + 1,
                retries,
                e,
            )
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise

    if last_error:
        raise last_error
    raise OpenRouterAPIError(500, "OpenRouter call failed after retries")


def parse_llm_json(raw_text: str) -> List[Dict[str, Any]]:
    if not raw_text or raw_text.strip() == "[]":
        return []

    text = raw_text.strip()

    try:
        payload = json.loads(text)
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return payload.get("issues", payload.get("items", []))
        return []
    except json.JSONDecodeError as e:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            try:
                payload = json.loads(match.group(1))
                if isinstance(payload, list):
                    return payload
                if isinstance(payload, dict):
                    return payload.get("issues", payload.get("items", []))
            except json.JSONDecodeError:
                pass

        logger.critical(f"Systemic Structured Failure: Response unparseable: {e}")
        return []


def normalize_issues(issues: List[Dict[str, Any]], filename: str) -> List[Dict[str, Any]]:
    clean = []
    REQUIRED_FIELDS = {"line_start", "line_end", "severity", "issue", "suggestion"}
    VALID_SEVERITIES = {"CRITICAL", "WARNING", "SUGGESTION"}

    for item in issues:
        if not REQUIRED_FIELDS.issubset(item.keys()):
            continue

        item["severity"] = str(item["severity"]).upper()
        if item["severity"] not in VALID_SEVERITIES:
            item["severity"] = "SUGGESTION"

        item.setdefault("category", "DSA")
        item.setdefault("complexity_before", "")
        item.setdefault("complexity_after", "")
        item.setdefault("pattern", "")
        item["file"] = filename

        clean.append(item)
    return clean


def analyze_chunk(filename: str, language: str, patch: str, total_lines: int = 0) -> List[Dict[str, Any]]:
    user_prompt = build_user_prompt(filename, language, patch, total_lines)
    provider = getattr(settings, "LLM_PROVIDER", "openrouter").strip().lower()

    if provider == "openai":
        raw = OpenAIProvider().call_model(DSA_SYSTEM_PROMPT, user_prompt)
    elif provider == "gemini":
        raw = GeminiProvider().call_model(DSA_SYSTEM_PROMPT, user_prompt)
    else:
        raw = call_llm(DSA_SYSTEM_PROMPT, user_prompt)

    issues = parse_llm_json(raw)
    return normalize_issues(issues, filename)