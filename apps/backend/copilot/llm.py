"""Replaceable LLM provider.

``OfflineProvider`` performs no model call: the composer then renders a deterministic,
fully grounded answer from tool outputs and retrieved chunks. ``AnthropicProvider`` asks
Claude to write the narrative from the same evidence bundle; its output is accepted only
if every citation marker it uses exists in the bundle (see ``compose.validate_citations``),
otherwise the offline rendering is used.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

from common.logging import log_event

log = logging.getLogger("copilot.llm")


@dataclass
class LLMResult:
    text: str | None
    model: str
    latency_ms: float
    error: str | None = None


class LLMProvider(Protocol):
    name: str

    async def generate(self, system: str, user: str) -> LLMResult: ...


class OfflineProvider:
    name = "offline"

    async def generate(self, system: str, user: str) -> LLMResult:
        return LLMResult(text=None, model="offline-composer", latency_ms=0.0)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None, model: str = "claude-opus-5-5", timeout_s: float = 60.0) -> None:
        import anthropic  # optional dependency

        # api_key=None lets the SDK resolve credentials from the environment.
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_s, max_retries=2)
        self._anthropic = anthropic
        self.model = model

    async def generate(self, system: str, user: str) -> LLMResult:
        started = time.perf_counter()
        try:
            resp = await self._client.beta.messages.create(
                model=self.model,
                max_tokens=4000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except self._anthropic.APIStatusError as exc:
            return self._fail(started, f"api_status_{exc.status_code}")
        except self._anthropic.APIConnectionError:
            return self._fail(started, "api_connection_error")
        latency = (time.perf_counter() - started) * 1000
        if resp.stop_reason == "refusal":
            return self._fail(started, "refusal")
        text = "".join(str(getattr(b, "text", "")) for b in resp.content if getattr(b, "type", "") == "text").strip()
        log_event(log, "llm.generate", model=self.model, llm_latency_ms=round(latency, 1), output_tokens=getattr(resp.usage, "output_tokens", None))
        return LLMResult(text=text or None, model=self.model, latency_ms=latency)

    def _fail(self, started: float, error: str) -> LLMResult:
        latency = (time.perf_counter() - started) * 1000
        log_event(log, "llm.generate.failed", level=logging.WARNING, model=self.model, error=error, llm_latency_ms=round(latency, 1))
        return LLMResult(text=None, model=self.model, latency_ms=latency, error=error)


def build_provider(name: str, api_key: str | None, model: str, timeout_s: float) -> LLMProvider:
    if name == "anthropic":
        return AnthropicProvider(api_key, model, timeout_s)
    return OfflineProvider()
