import json
import logging
import re
from typing import List, Dict, Any
from django.conf import settings
from analyzer.prompts import build_user_prompt, DSA_SYSTEM_PROMPT
from analyzer.rotator import acquire_healthy_key_slot, penalize_slot
from .models import LLMKeyRingSlot
from django.db import models

logger = logging.getLogger(__name__)


def _sanitize_llm_text(text: str) -> str:
    """
    Two-pass sanitizer for raw LLM output:

    Pass 1 — strip markdown fences and remove truly non-printable bytes.
    Pass 2 — state-machine walk: inside a JSON string value, replace literal
              \\n / \\r / \\t with their JSON escape sequences so json.loads
              can succeed.  Outside strings these characters are valid JSON
              whitespace and are left alone.
    """
    # Pass 1: strip markdown fences + non-printable control chars
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"```\s*$",          "", text, flags=re.MULTILINE)
    text = text.strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    # Pass 2: fix literal \n / \r / \t embedded inside JSON string values
    _ESCAPE = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    result: list[str] = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and in_string:
            # Already-escaped sequence — pass both chars through unchanged
            result.append(ch)
            i += 1
            if i < len(text):
                result.append(text[i])
                i += 1
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
            continue
        if in_string and ch in _ESCAPE:
            result.append(_ESCAPE[ch])
        else:
            result.append(ch)
        i += 1
    return "".join(result)


def _try_parse(text: str) -> List[Dict[str, Any]] | None:
    """Attempt json.loads and return the list, or None on failure."""
    for candidate in (text,):
        try:
            # strict=False allows literal newlines inside fixed_code strings (common LLM mistake)
            payload = json.loads(candidate, strict=False)
            if isinstance(payload, list):
                return payload
            if isinstance(payload, dict):
                return payload.get("issues", payload.get("items", []))
        except json.JSONDecodeError:
            pass
    return None


def parse_llm_json(raw_text: str) -> tuple[List[Dict[str, Any]], bool]:
    """
    Safely extracts and parses JSON array objects out of raw LLM outputs.
    Returns (issues, parse_ok). parse_ok=False means the model returned data we could not read.
    """
    if not raw_text or raw_text.strip() == "[]":
        return [], True

    # Pass 1: sanitize and try a direct parse
    text = _sanitize_llm_text(raw_text)
    result = _try_parse(text)
    if result is not None:
        return result, True

    # Pass 2: regex extraction — handles responses where JSON is buried in prose
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if match:
        result = _try_parse(match.group(1))
        if result is not None:
            return result, True

    logger.critical(
        "Systemic Structured Failure: Response unparseable after sanitization — first 200 chars: %r",
        text[:200],
    )
    return [], False


def normalize_issues(issues: List[Dict[str, Any]], filename: str) -> List[Dict[str, Any]]:
    """Validates structural parameters and unifies schema naming limits."""
    clean = []
    REQUIRED_FIELDS = {"line_start", "line_end", "severity", "issue", "suggestion"}
    VALID_SEVERITIES = {"CRITICAL", "WARNING", "SUGGESTION"}

    for item in issues:
        # LLMs sometimes emit "line" instead of "line_start"
        if "line_start" not in item and "line" in item:
            item["line_start"] = item["line"]
        if "line_end" not in item and "line_start" in item:
            item["line_end"] = item["line_start"]

        if not REQUIRED_FIELDS.issubset(item.keys()):
            logger.debug("Skipping issue missing fields in %s: %s", filename, item.keys())
            continue

        item["severity"] = str(item["severity"]).upper()
        if item["severity"] not in VALID_SEVERITIES:
            item["severity"] = "SUGGESTION"

        item.setdefault("category", "DSA")
        item.setdefault("complexity_before", "")
        item.setdefault("complexity_after", "")
        item.setdefault("pattern", "")
        item["file"] = filename

        fc = item.get("fixed_code")
        if isinstance(fc, dict):
            item["fixed_code"] = {
                "before": str(fc.get("before", "") or ""),
                "after": str(fc.get("after", "") or ""),
            }
        elif fc is None:
            item["fixed_code"] = {"before": "", "after": ""}

        clean.append(item)
    return clean


# Returned as the second element of analyze_chunk() when the file could not be analyzed.
CHUNK_ERROR_PARSE_FAILED = "parse_failed"
CHUNK_ERROR_SERVICE_UNAVAILABLE = "service_unavailable"
CHUNK_ERROR_PROVIDER_EXHAUSTED = "provider_exhausted"


def analyze_chunk(filename: str, language: str,
                  patch: str, total_lines: int = 0) -> tuple[list[dict], str | None]:

    from analyzer.providers.gemini import GeminiProvider, GeminiAPIError
    from analyzer.providers.openai import OpenAIProvider, OpenAIAPIError
    from analyzer.providers.openrouter import OpenRouterProvider, OpenRouterAPIError

    PROVIDER_MAP = {
        "openrouter": OpenRouterProvider,
        "openai":     OpenAIProvider,
        "gemini":     GeminiProvider,
    }

    user_prompt  = build_user_prompt(filename, language, patch, total_lines)
    max_attempts = 6  
    last_error   = None

    for attempt in range(max_attempts):
        try:
            slot = acquire_healthy_key_slot()
        except RuntimeError as e:
            logger.error(f"Key rotator completely exhausted: {e}")
            return [], CHUNK_ERROR_SERVICE_UNAVAILABLE

        provider_class = PROVIDER_MAP.get(slot.provider, GeminiProvider)

        try:
            # Enforce dynamic parameters directly from the claimed database row slot
            provider = provider_class(
                api_key=slot.key_value,
                model=slot.model_override or None,
            )
            raw = provider.call_model(DSA_SYSTEM_PROMPT, user_prompt)

            # Success tracking increment
            LLMKeyRingSlot.objects.filter(id=slot.id).update(
                total_calls_handled=models.F("total_calls_handled") + 1
            )

            issues, parse_ok = parse_llm_json(raw)
            if not parse_ok:
                logger.error("%s → LLM response could not be parsed (fixed_code/newlines?)", filename)
                return [], CHUNK_ERROR_PARSE_FAILED

            result = normalize_issues(issues, filename)
            logger.info(f"{filename} → {len(result)} issues [{slot.provider} slot#{slot.id}]")
            return result, None

        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            # Safely read error message details generated inside the providers
            error_message = str(exc)
            last_error  = exc

            if status_code:
                # STEP 1: Forward status code and error messages to look for credit limits
                penalize_slot(slot, status_code, error_message=error_message)

            if status_code in (400, 401, 402, 403):
                logger.error(f"Hard structural outage ({status_code}) on slot#{slot.id} — aborting provider path.")

            logger.warning(
                f"Attempt {attempt+1}/{max_attempts} failed "
                f"[{slot.provider} slot#{slot.id}]: {exc}"
            )

    logger.error(f"All structural backup attempts ({max_attempts}) failed for {filename}")
    return [], CHUNK_ERROR_PROVIDER_EXHAUSTED