import json
import logging
import requests
from typing import List, Dict, Any
from django.conf import settings
from analyzer.prompts import DSA_SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)

# Definitive JSON Schema to guarantee Gemini outputs matching your internal architecture
GEMINI_OUTPUT_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "line_start": {"type": "integer"},
            "line_end": {"type": "integer"},
            "severity": {"type": "string", "enum": ["CRITICAL", "WARNING", "SUGGESTION"]},
            "category": {"type": "string"},
            "issue": {"type": "string"},
            "suggestion": {"type": "string"},
            "complexity_before": {"type": "string"},
            "complexity_after": {"type": "string"},
            "pattern": {"type": "string"}
        },
        "required": ["line_start", "line_end", "severity", "issue", "suggestion"]
    }
}

def call_gemini(system_prompt: str, user_prompt: str, retries: int = 3) -> str:
    """Raw Gemini API call securing credentials and leveraging schema constraints."""
    # Production Fix: Secure key insertion via headers rather than query params to protect log exposure
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": settings.GEMINI_API_KEY
    }

    body = {
        "system_instruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": [
            {"role": "user", "parts": [{"text": user_prompt}]}
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 4096,
            # Production Fix: Enforce structured output schema natively at the API boundary
            "responseMimeType": "application/json",
            "responseSchema": GEMINI_OUTPUT_SCHEMA
        },
    }

    for attempt in range(retries):
        try:
            resp = requests.post(GEMINI_URL, json=body, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

        except requests.exceptions.RequestException as e:
            logger.error(f"Gemini core connection network error (attempt {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                import time
                time.sleep(2 ** attempt)
            else:
                raise


def parse_gemini_json(raw_text: str) -> List[Dict[str, Any]]:
    """Safely extracts data array objects out of certified JSON payload strings."""
    if not raw_text or raw_text.strip() == "[]":
        return []

    try:
        issues = json.loads(raw_text.strip())
        return issues if isinstance(issues, list) else []
    except json.JSONDecodeError as e:
        logger.critical(f"Systemic Structured Failure: Output broken despite schema constraints: {e}")
        return []


def normalize_issues(issues: List[Dict[str, Any]], filename: str) -> List[Dict[str, Any]]:
    """Validates parameters, unifies naming limits, and bounds structural arrays."""
    clean = []
    REQUIRED_FIELDS = {"line_start", "line_end", "severity", "issue", "suggestion"}
    VALID_SEVERITIES = {"CRITICAL", "WARNING", "SUGGESTION"}

    for item in issues:
        if not REQUIRED_FIELDS.issubset(item.keys()):
            continue

        item["severity"] = item["severity"].upper()
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
    """Isolated extraction pipeline analyzing an isolated file block component."""
    user_prompt = build_user_prompt(filename, language, patch, total_lines)
    try:
        raw = call_gemini(DSA_SYSTEM_PROMPT, user_prompt)
        issues = parse_gemini_json(raw)
        return normalize_issues(issues, filename)
    except Exception as e:
        logger.error(f"Failed to analyze chunk for asset {filename}: {e}")
        return []