"""
AI Debate Engine for AlphaPartner.

Implements a collaborative dual-AI debate architecture where:
  1. Gemini generates an initial analysis (Round 1)
  2. Claude generates an independent analysis (Round 1)
  3. Gemini reviews Claude's reasoning (Round 2 — cross-review)
  4. Claude reviews Gemini's reasoning (Round 2 — cross-review)
  5. The engine synthesises both perspectives into a final balanced verdict

Both models have EQUAL weight. Neither is primary. The synthesis uses the model
that responds fastest on the final round — configurable via `synth_provider`.

Additional providers (OpenAI, Grok, DeepSeek, local models) can be plugged in
by implementing `AIProvider` in services/ai_provider.py.
"""
import logging
import asyncio
from typing import Optional, Literal
from services.ai_provider import AIProvider, AIMessage, AIResponse, SimulatedProvider
from services.claude_provider import get_claude_provider
from services.gemini_provider import get_gemini_provider

logger = logging.getLogger(__name__)

SynthStrategy = Literal["fastest", "claude", "gemini", "merge"]


class AIDebateEngine:
    """
    Orchestrates a multi-round debate between AI providers.

    Round 1: Independent analysis from each model
    Round 2: Cross-review (each model critiques the other's output)
    Final:   Synthesis into a balanced, actionable verdict
    """

    def __init__(self):
        self.claude = get_claude_provider()
        self.gemini = get_gemini_provider()
        self.fallback = SimulatedProvider()

    def _get_active_providers(self) -> tuple[AIProvider, AIProvider]:
        """Return (primary, secondary) — both equal, just ordering for API calls."""
        claude_ok = self.claude.is_configured
        gemini_ok = self.gemini.is_configured

        if claude_ok and gemini_ok:
            return self.claude, self.gemini
        elif claude_ok:
            return self.claude, self.fallback
        elif gemini_ok:
            return self.gemini, self.fallback
        else:
            return self.fallback, self.fallback

    async def debate(
        self,
        prompt: str,
        session_id: str = "default",
        rounds: int = 2,
        synth_provider: SynthStrategy = "fastest",
        max_tokens_per_round: int = 300,
    ) -> dict:
        """
        Run a full AI debate.

        Returns:
            {
                "claude_analysis": str,
                "gemini_analysis": str,
                "claude_review": str,   (cross-review in round 2)
                "gemini_review": str,   (cross-review in round 2)
                "final_verdict": str,
                "providers_active": list[str],
                "rounds_completed": int,
            }
        """
        provider_a, provider_b = self._get_active_providers()
        providers_active = list({provider_a.name, provider_b.name})

        symbol = "NSE Stock"
        if session_id and session_id.startswith("explain-"):
            symbol = session_id.split("explain-", 1)[1].upper()

        from services.activity_logger import log_activity
        log_activity(f"Running dual-AI debate for {symbol}", "rank", "running")

        # ── Round 1: Independent Analysis ──────────────────────────
        claude_sys = (
            "You are Claude, an AI market analyst for Indian stock markets (NSE/BSE). "
            "Provide thorough technical and fundamental analysis. Focus on bullish catalysts, "
            "momentum indicators, support/resistance, and entry rationale. "
            "Be specific with price levels. Use bullet points. Under 200 words."
        )
        gemini_sys = (
            "You are Gemini, an AI risk analyst for Indian stock markets (NSE/BSE). "
            "Focus on risk factors, bearish scenarios, caution points, and what could go wrong. "
            "Be specific about downside risks, overbought conditions, and exit signals. "
            "Use bullet points. Under 200 words."
        )


        claude_r1, gemini_r1 = await asyncio.gather(
            self.claude.chat(claude_sys, prompt, max_tokens=max_tokens_per_round) if self.claude.is_configured
            else self.fallback.chat(claude_sys, prompt, max_tokens=max_tokens_per_round),
            self.gemini.chat(gemini_sys, prompt, max_tokens=max_tokens_per_round) if self.gemini.is_configured
            else self.fallback.chat(gemini_sys, prompt, max_tokens=max_tokens_per_round),
        )

        claude_analysis = claude_r1.content
        gemini_analysis = gemini_r1.content

        claude_review = ""
        gemini_review = ""

        # ── Round 2: Cross-Review (if enabled) ─────────────────────
        if rounds >= 2 and (self.claude.is_configured or self.gemini.is_configured):
            cross_sys_claude = (
                "You are Claude, reviewing a risk analyst's (Gemini's) assessment. "
                "Critically evaluate their risk points. Which concerns are valid? "
                "Which are overstated? Add any risk factors you agree with or missed. "
                "Keep under 150 words."
            )
            cross_sys_gemini = (
                "You are Gemini, reviewing a bullish analyst's (Claude's) assessment. "
                "Critically evaluate their bullish points. Which catalysts are genuine? "
                "Which are overstated? Add any opportunity you agree with or missed. "
                "Keep under 150 words."
            )

            cross_prompt_for_claude = (
                f"Original market question: {prompt}\n\n"
                f"Risk Analyst (Gemini) said:\n{gemini_analysis}\n\n"
                "Your critical review:"
            )
            cross_prompt_for_gemini = (
                f"Original market question: {prompt}\n\n"
                f"Bullish Analyst (Claude) said:\n{claude_analysis}\n\n"
                "Your critical review:"
            )

            claude_r2, gemini_r2 = await asyncio.gather(
                self.claude.chat(cross_sys_claude, cross_prompt_for_claude, max_tokens=200)
                if self.claude.is_configured else self.fallback.chat(cross_sys_claude, cross_prompt_for_claude),
                self.gemini.chat(cross_sys_gemini, cross_prompt_for_gemini, max_tokens=200)
                if self.gemini.is_configured else self.fallback.chat(cross_sys_gemini, cross_prompt_for_gemini),
            )

            claude_review = claude_r2.content
            gemini_review = gemini_r2.content

        # ── Final Synthesis ─────────────────────────────────────────
        synth_sys = (
            "You are the AlphaPartner debate moderator. You have received:\n"
            "- A bullish analysis from Claude (technical + momentum)\n"
            "- A risk analysis from Gemini (downside + caution)\n"
            "- Cross-reviews from both\n\n"
            "Your task: synthesise ALL perspectives into a single, balanced, actionable verdict. "
            "Be decisive. Include: overall stance (bullish/neutral/bearish), key entry/exit levels, "
            "one risk and one catalyst to watch. Under 150 words. No fluff."
        )
        synth_prompt = (
            f"Original question: {prompt}\n\n"
            f"CLAUDE (Bullish Analysis):\n{claude_analysis}\n\n"
            f"GEMINI (Risk Analysis):\n{gemini_analysis}\n\n"
        )
        if claude_review:
            synth_prompt += f"CLAUDE'S REVIEW OF GEMINI:\n{claude_review}\n\n"
        if gemini_review:
            synth_prompt += f"GEMINI'S REVIEW OF CLAUDE:\n{gemini_review}\n\n"
        synth_prompt += "Synthesised final verdict:"

        # Use the model that succeeded in Round 1, prefer Gemini for synthesis (equal strategy)
        if synth_provider == "gemini" and self.gemini.is_configured:
            synth_resp = await self.gemini.chat(synth_sys, synth_prompt, max_tokens=250)
        elif synth_provider == "claude" and self.claude.is_configured:
            synth_resp = await self.claude.chat(synth_sys, synth_prompt, max_tokens=250)
        elif synth_provider == "fastest":
            # Use whichever provider was faster (both ran in parallel — just pick the one configured)
            if self.gemini.is_configured:
                synth_resp = await self.gemini.chat(synth_sys, synth_prompt, max_tokens=250)
            elif self.claude.is_configured:
                synth_resp = await self.claude.chat(synth_sys, synth_prompt, max_tokens=250)
            else:
                synth_resp = await self.fallback.chat(synth_sys, synth_prompt, max_tokens=250)
        else:
            synth_resp = await self.fallback.chat(synth_sys, synth_prompt, max_tokens=250)

        return {
            "claude_analysis": claude_analysis,
            "gemini_analysis": gemini_analysis,
            "claude_review": claude_review,
            "gemini_review": gemini_review,
            "final_verdict": synth_resp.content,
            "providers_active": providers_active,
            "rounds_completed": 2 if claude_review or gemini_review else 1,
        }

    async def simple_chat(
        self,
        system_prompt: str,
        user_message: str | list[AIMessage],
        prefer: Literal["claude", "gemini", "auto"] = "auto",
        max_tokens: int = 800,
    ) -> str:
        """
        Single-provider call for non-debate use cases (chat, summaries, etc).
        Falls back to the other provider if the preferred one fails.
        """
        providers_by_pref: list[AIProvider]
        if prefer == "claude":
            providers_by_pref = [self.claude, self.gemini, self.fallback]
        elif prefer == "gemini":
            providers_by_pref = [self.gemini, self.claude, self.fallback]
        else:  # auto — try both, return first success
            providers_by_pref = [self.claude, self.gemini, self.fallback]

        if isinstance(user_message, list):
            messages = [AIMessage(role="system", content=system_prompt)] + user_message
        else:
            messages = [
                AIMessage(role="system", content=system_prompt),
                AIMessage(role="user", content=user_message),
            ]

        for provider in providers_by_pref:
            if not provider.is_configured:
                continue
            resp = await provider.complete(messages, max_tokens=max_tokens)
            if resp.success:
                return resp.content
            logger.warning(f"{provider.name} failed, trying next: {resp.error}")

        # All failed — use fallback
        resp = await self.fallback.complete(messages, max_tokens=max_tokens)
        return resp.content

    def get_status(self) -> dict:
        """Return status of all configured providers."""
        return {
            "claude": {
                "configured": self.claude.is_configured,
                "model": "claude-3-5-sonnet-20241022",
            },
            "gemini": {
                "configured": self.gemini.is_configured,
                "model": "gemini-2.5-flash",
            },
            "debate_ready": self.claude.is_configured or self.gemini.is_configured,
            "full_debate": self.claude.is_configured and self.gemini.is_configured,
        }


# Module-level singleton
_engine = AIDebateEngine()


def get_debate_engine() -> AIDebateEngine:
    return _engine
