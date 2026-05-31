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


def parse_llm_json(raw_text: str) -> List[Dict[str, Any]]:
    """Safely extracts and parses JSON array objects out of raw LLM outputs."""
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
        # Regex extraction fallback if the LLM wrapped JSON in markdown blocks
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
    """Validates structural parameters and unifies schema naming limits."""
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



def analyze_chunk(filename: str, language: str,
                  patch: str, total_lines: int = 0) -> list[dict]:

    from analyzer.providers.gemini import GeminiProvider, GeminiAPIError
    from analyzer.providers.openai import OpenAIProvider, OpenAIAPIError
    from analyzer.providers.openrouter import OpenRouterProvider, OpenRouterAPIError

    PROVIDER_MAP = {
        "openrouter": OpenRouterProvider,
        "openai":     OpenAIProvider,
        "gemini":     GeminiProvider,
    }

    user_prompt  = build_user_prompt(filename, language, patch, total_lines)
    max_attempts = 6  # enough to try all 3 tiers with 2 keys each
    last_error   = None

    for attempt in range(max_attempts):
        try:
            slot = acquire_healthy_key_slot()
        except RuntimeError as e:
            logger.error(f"Key rotator exhausted: {e}")
            return []  # Graceful degradation — don't crash the task

        provider_class = PROVIDER_MAP.get(slot.provider, OpenRouterProvider)

        try:
            # ✅ Key passed directly — no settings mutation, thread-safe
            provider = provider_class(
                api_key=slot.key_value,
                model=slot.model_override or None,
            )
            raw = provider.call_model(DSA_SYSTEM_PROMPT, user_prompt)

            # Success — update call counter
            LLMKeyRingSlot.objects.filter(id=slot.id).update(
                total_calls_handled=models.F("total_calls_handled") + 1
            )

            issues = parse_llm_json(raw)
            result = normalize_issues(issues, filename)
            logger.info(f"{filename} → {len(result)} issues [{slot.provider} slot#{slot.id}]")
            return result

        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            last_error  = exc

            if status_code:
                penalize_slot(slot, status_code)

            if status_code in (400, 401, 403):
                # Config error — don't retry same provider type
                logger.error(f"Hard error {status_code} on slot#{slot.id} — skipping provider")

            logger.warning(
                f"Attempt {attempt+1}/{max_attempts} failed "
                f"[{slot.provider} slot#{slot.id}]: {exc}"
            )

    logger.error(f"All {max_attempts} attempts failed for {filename}")
    return []