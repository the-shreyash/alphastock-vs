"""
Claude (Anthropic) AI Provider for AlphaPartner.

Uses the official `anthropic` SDK directly — no Emergent wrapper required.
Configure with environment variable: ANTHROPIC_API_KEY
"""
import os
import logging
from typing import Optional
from services.ai_provider import AIProvider, AIMessage, AIResponse

logger = logging.getLogger(__name__)

# Default model — override via model parameter
CLAUDE_DEFAULT_MODEL = "claude-3-5-sonnet-20241022"
CLAUDE_FAST_MODEL = "claude-3-5-haiku-20241022"


def _get_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


class ClaudeProvider(AIProvider):
    """Anthropic Claude provider using the official SDK."""

    @property
    def name(self) -> str:
        return "claude"

    @property
    def is_configured(self) -> bool:
        return bool(_get_key())

    @property
    def default_model(self) -> str:
        return CLAUDE_DEFAULT_MODEL

    async def complete(
        self,
        messages: list[AIMessage],
        model: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> AIResponse:
        key = _get_key()
        if not key:
            return AIResponse(
                content="Claude AI is not configured. Please add ANTHROPIC_API_KEY.",
                provider="claude",
                model=model or self.default_model,
                error="missing_api_key",
            )

        target_model = model or self.default_model

        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=key)

            # Separate system message from conversation
            system_msg = ""
            conversation = []
            for m in messages:
                if m.role == "system":
                    system_msg = m.content
                else:
                    conversation.append({"role": m.role, "content": m.content})

            # Ensure at least one user message
            if not conversation:
                conversation = [{"role": "user", "content": "Hello"}]

            response = await client.messages.create(
                model=target_model,
                max_tokens=max_tokens,
                system=system_msg,
                messages=conversation,
            )
            text = response.content[0].text if response.content else ""
            return AIResponse(content=text, provider="claude", model=target_model)

        except Exception as e:
            error_str = str(e)
            logger.error(f"Claude provider error: {error_str}")

            # Friendly error messages
            if "authentication" in error_str.lower() or "401" in error_str:
                msg = "Claude API key is invalid. Please check ANTHROPIC_API_KEY."
            elif "rate" in error_str.lower() or "429" in error_str:
                msg = "Claude rate limit reached. Please wait a moment and try again."
            elif "overloaded" in error_str.lower() or "529" in error_str:
                msg = "Claude servers are currently overloaded. Please retry shortly."
            else:
                msg = f"Claude analysis temporarily unavailable: {error_str[:120]}"

            return AIResponse(content=msg, provider="claude", model=target_model, error=error_str)


# Module-level singleton
_claude = ClaudeProvider()


def get_claude_provider() -> ClaudeProvider:
    return _claude


def is_configured() -> bool:
    return _claude.is_configured
