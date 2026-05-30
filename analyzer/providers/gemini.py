# analyzer/providers/gemini.py
import requests
from django.conf import settings
from analyzer.providers.base import BaseLLMProvider

class GeminiProvider(BaseLLMProvider):
    def call_model(self, system_prompt: str, user_prompt: str, response_schema: dict = None) -> str:
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": settings.GEMINI_API_KEY
        }
        
        body = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 4096
            }
        }
            
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
        resp = requests.post(url, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]