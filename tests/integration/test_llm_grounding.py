"""The LLM provider is replaceable; its narrative is accepted only if every citation is grounded."""

from copilot.llm import LLMResult
from copilot.orchestrator import Orchestrator
from copilot.state import ConversationStore

PROMPT = "Prepare an incident for the highest-priority active alarm in EastRefinery."


class FakeLLM:
    name = "fake"

    def __init__(self, text: str | None, error: str | None = None) -> None:
        self.text, self.error = text, error
        self.prompts: list[tuple[str, str]] = []

    async def generate(self, system: str, user: str) -> LLMResult:
        self.prompts.append((system, user))
        return LLMResult(text=self.text, model="fake-model", latency_ms=12.0, error=self.error)


def orch(registry, retriever, audit, llm):
    return Orchestrator(registry, retriever, llm, ConversationStore(), audit)


async def test_grounded_llm_answer_is_used(registry, retriever, audit):
    llm = FakeLLM("K-501 is surging [T3]. Follow the immediate response steps [D1]. A similar case was fixed by replacing a solenoid [INC-1008].")
    r = await orch(registry, retriever, audit, llm).handle(PROMPT, None)
    assert "K-501 is surging [T3]" in r["answer_markdown"]
    assert r["trace"]["llm"]["accepted"] is True
    system, user = llm.prompts[0]
    assert "untrusted" in system and "<document ref=" in user  # documents fenced as data


async def test_llm_answer_with_invented_citation_is_rejected(registry, retriever, audit):
    llm = FakeLLM("The pump exploded [D99].")
    r = await orch(registry, retriever, audit, llm).handle(PROMPT, None)
    assert "[D99]" not in r["answer_markdown"]
    assert r["trace"]["llm"]["accepted"] is False and r["trace"]["llm"]["unknown_citations"] == ["[D99]"]
    assert any("LLM narrative rejected" in w for w in r["warnings"])


async def test_llm_failure_falls_back_to_template(registry, retriever, audit):
    r = await orch(registry, retriever, audit, FakeLLM(None, error="api_connection_error")).handle(PROMPT, None)
    assert r["answer_markdown"].startswith("### Incident draft")
    assert r["trace"]["llm"]["error"] == "api_connection_error"
