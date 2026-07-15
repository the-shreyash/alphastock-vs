"""
Model Router for StockAssist AI (SAI).

Implements the "Master Orchestrator decides which model to use" responsibility
described in `.claude/AI_AGENT_SYSTEM.md`. The router is the single entry point
the rest of the platform uses to run an AI task: it picks the right model for
the job, runs it through the existing `AIDebateEngine` (which already handles
provider fall-back and the simulated offline provider), and — when the prompt
is defined in the Prompt Library — pulls both the system prompt and the routing
preference from there so behaviour stays consistent and centralized.

Routing philosophy (from AI_AGENT_SYSTEM.md → "Claude & Gemini Collaboration"):
  * Claude  → deep reasoning, portfolio review, risk analysis, complex reports,
              trade coaching. (accuracy-first)
  * Gemini  → fast responses, news summarization, market pulse, learning content.
              (speed / large-context-first)

The router never talks to provider SDKs directly — it delegates to the debate
engine so there is exactly one place that knows how to call Claude/Gemini and
how to degrade gracefully when keys are missing.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

from services.ai_debate_engine import get_debate_engine
from services.prompt_library import PROMPTS, get_prompt, get_prefer

logger = logging.getLogger(__name__)

Prefer = Literal["claude", "gemini", "auto"]


class ModelRouter:
    """Routes AI tasks to the appropriate model via the debate engine."""

    def __init__(self):
        self._engine = get_debate_engine()

    # ── Core routing ────────────────────────────────────────────────
    async def run(
        self,
        prompt_key: str,
        user_message: str,
        *,
        prefer: Optional[Prefer] = None,
        max_tokens: int = 800,
        **prompt_kwargs,
    ) -> dict:
        """Run a Prompt-Library-defined task.

        `prompt_key` must exist in the Prompt Library. The system prompt and
        default model preference are taken from the library; `prefer` overrides
        the library default when supplied.

        Returns a structured dict so callers can surface which model answered
        (transparency requirement from AI_AGENT_SYSTEM.md).
        """
        if prompt_key not in PROMPTS:
            raise KeyError(f"Unknown prompt key: {prompt_key}")

        system_prompt = get_prompt(prompt_key, **prompt_kwargs)
        chosen = prefer or get_prefer(prompt_key)
        content = await self._engine.simple_chat(
            system_prompt, user_message, prefer=chosen, max_tokens=max_tokens
        )
        return {
            "content": content,
            "prompt_key": prompt_key,
            "prompt_version": PROMPTS[prompt_key].version,
            "model_preference": chosen,
            "model_used": self._resolve_used(chosen),
        }

    async def run_raw(
        self,
        system_prompt: str,
        user_message,
        *,
        prefer: Prefer = "auto",
        max_tokens: int = 800,
    ) -> str:
        """Escape hatch for ad-hoc prompts not (yet) in the Prompt Library.

        Prefer :meth:`run` with a library key. This exists so existing call
        sites can migrate incrementally without a big-bang refactor.
        """
        return await self._engine.simple_chat(
            system_prompt, user_message, prefer=prefer, max_tokens=max_tokens
        )

    # ── Introspection ───────────────────────────────────────────────
    def resolve_provider(self, prefer: Prefer) -> str:
        """Public: the provider (``claude`` | ``gemini`` | ``simulated``) that
        will actually serve a request for the given preference, mirroring the
        debate engine's fall-back order. Lets callers label live AI activity
        with the real model instead of the requested one (Sprint R7)."""
        return self._resolve_used(prefer)

    def _resolve_used(self, prefer: Prefer) -> str:
        """Best-effort report of which provider actually served a request.

        Mirrors the fall-back order inside `AIDebateEngine.simple_chat`.
        """
        claude_ok = self._engine.claude.is_configured
        gemini_ok = self._engine.gemini.is_configured
        if prefer == "claude":
            if claude_ok:
                return "claude"
            return "gemini" if gemini_ok else "simulated"
        if prefer == "gemini":
            if gemini_ok:
                return "gemini"
            return "claude" if claude_ok else "simulated"
        # auto — engine tries claude first, then gemini
        if claude_ok:
            return "claude"
        if gemini_ok:
            return "gemini"
        return "simulated"

    def status(self) -> dict:
        """Report router + provider health and the task→model routing table.

        Powers the `/api/ai/status` endpoint and the frontend model-status pill.
        """
        engine_status = self._engine.get_status()
        routing = {
            key: {"role": p.role, "prefer": p.prefer, "version": p.version}
            for key, p in PROMPTS.items()
        }
        return {
            "providers": {
                "claude": engine_status["claude"],
                "gemini": engine_status["gemini"],
            },
            "debate_ready": engine_status["debate_ready"],
            "full_debate": engine_status["full_debate"],
            "online": engine_status["debate_ready"],
            "routing": routing,
            "policy": {
                "claude": "deep reasoning, portfolio, risk, reports, coaching",
                "gemini": "fast responses, news, market pulse, learning",
            },
        }


# Module-level singleton
_router = ModelRouter()


def get_model_router() -> ModelRouter:
    return _router
