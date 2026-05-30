# analyzer/providers/base.py
from abc import ABC, abstractmethod

class BaseLLMProvider(ABC):
    """Abstract Base Class enforcing a uniform payload footprint across all AI operators."""
    
    @abstractmethod
    def call_model(self, system_prompt: str, user_prompt: str, response_schema: dict = None) -> str:
        """Executes a synchronous network call to the target LLM and returns the raw string content."""
        pass