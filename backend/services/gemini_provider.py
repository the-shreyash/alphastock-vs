"""
Gemini (Google) AI Provider for AlphaPartner.

Uses the official `google-genai` SDK directly — no Emergent wrapper required.
Configure with environment variable: GOOGLE_GEMINI_KEY
"""
import os
import logging
from typing import Optional
from services.ai_provider import AIProvider, AIMessage, AIResponse

logger = logging.getLogger(__name__)

# Default models
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"
GEMINI_FAST_MODEL = "gemini-2.0-flash"


def _get_key() -> str:
    return os.environ.get("GOOGLE_GEMINI_KEY", "").strip()


class GeminiProvider(AIProvider):
    """Google Gemini provider using the official google-genai SDK."""

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def is_configured(self) -> bool:
        return bool(_get_key())

    @property
    def default_model(self) -> str:
        return GEMINI_DEFAULT_MODEL

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
                content="Gemini AI is not configured. Please add GOOGLE_GEMINI_KEY.",
                provider="gemini",
                model=model or self.default_model,
                error="missing_api_key",
            )

        target_model = model or self.default_model

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=key)

            # Build the contents for Gemini — combine system + user into structured prompt
            system_msg = ""
            conversation_parts = []
            for m in messages:
                if m.role == "system":
                    system_msg = m.content
                elif m.role == "user":
                    conversation_parts.append(m.content)
                elif m.role == "assistant":
                    conversation_parts.append(f"[Previous response]: {m.content}")

            # Prepend system message to first user message for Gemini (no native system role)
            if system_msg and conversation_parts:
                conversation_parts[0] = f"{system_msg}\n\n{conversation_parts[0]}"
            elif system_msg:
                conversation_parts = [system_msg]

            full_prompt = "\n\n".join(conversation_parts)

            response = await client.aio.models.generate_content(
                model=target_model,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                ),
            )
            text = response.text or ""
            return AIResponse(content=text, provider="gemini", model=target_model)

        except Exception as e:
            error_str = str(e)
            logger.error(f"Gemini provider error: {error_str}")

            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                msg = (
                    "Gemini rate limit reached (free tier). "
                    "Quota resets daily. Visit ai.google.dev/pricing to upgrade."
                )
            elif "API_KEY_INVALID" in error_str or "401" in error_str:
                msg = "Gemini API key is invalid. Please check GOOGLE_GEMINI_KEY."
            elif "SAFETY" in error_str:
                msg = "Gemini blocked this request due to safety filters. Please rephrase."
            else:
                msg = f"Gemini analysis temporarily unavailable: {error_str[:120]}"

            return AIResponse(content=msg, provider="gemini", model=target_model, error=error_str)


# Module-level singleton
_gemini = GeminiProvider()


def get_gemini_provider() -> GeminiProvider:
    return _gemini


def is_configured() -> bool:
    return _gemini.is_configured
