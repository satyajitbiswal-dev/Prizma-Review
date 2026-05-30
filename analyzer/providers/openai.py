# analyzer/providers/openai.py
import requests
from django.conf import settings
from analyzer.providers.base import BaseLLMProvider

class OpenAIProvider(BaseLLMProvider):
    def call_model(self, system_prompt: str, user_prompt: str, response_schema: dict = None) -> str:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}"
        }
        
        body = {
            "model": getattr(settings, "OPENAI_MODEL", "gpt-4o"),
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        }
            
        resp = requests.post("https://api.openai.com/v1/chat/completions", json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]