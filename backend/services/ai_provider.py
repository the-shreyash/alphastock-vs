"""
AI Provider Abstraction Layer for AlphaPartner.

Defines the base interface all AI providers must implement.
Supports: Claude (Anthropic), Gemini (Google), and extensible to OpenAI, Grok, DeepSeek, local models.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class AIMessage:
    """A single message in a conversation."""
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class AIResponse:
    """A response from an AI provider."""
    content: str
    provider: str
    model: str
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


class AIProvider(ABC):
    """Abstract base class for all AI providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""
        ...

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the provider has valid credentials."""
        ...

    @abstractmethod
    async def complete(
        self,
        messages: list[AIMessage],
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> AIResponse:
        """Send messages and return a completion response."""
        ...

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> AIResponse:
        """Convenience wrapper for single-turn system+user calls."""
        messages = [
            AIMessage(role="system", content=system_prompt),
            AIMessage(role="user", content=user_message),
        ]
        return await self.complete(messages, model=model, max_tokens=max_tokens, temperature=temperature)


class SimulatedProvider(AIProvider):
    """Fallback provider that returns template responses when no API keys are configured."""

    @property
    def name(self) -> str:
        return "simulated"

    @property
    def is_configured(self) -> bool:
        return True  # Always available as fallback

    async def complete(
        self,
        messages: list[AIMessage],
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> AIResponse:
        text = (
            "AI services are currently offline or unavailable. "
            "Please check that ANTHROPIC_API_KEY and GOOGLE_GEMINI_KEY are configured in your backend .env file "
            "and have active billing limits/credits."
        )
        return AIResponse(content=text, provider="simulated", model="template", error="live_connection_failed")
