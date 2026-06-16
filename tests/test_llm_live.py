"""Live integration tests for LLM providers.

Skipped unless the corresponding API key env var is set.
Run with: uv run python -m pytest tests/test_llm_live.py -v

These exercise the REAL provider SDKs and therefore validate the field
assumptions the LLMResult refactor depends on (token counts, latency,
raw_text, and — best-effort — cache/reasoning fields). Mocked unit tests
cannot catch a wrong SDK attribute name; these can.
"""
import os
import pytest
from pydantic import BaseModel

from skyward.llm.providers import LLMResult


class SimpleAnswer(BaseModel):
    answer: str


requires_openai = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set"
)
requires_gemini = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"), reason="GEMINI_API_KEY not set"
)
requires_perplexity = pytest.mark.skipif(
    not os.environ.get("PERPLEXITY_API_KEY"), reason="PERPLEXITY_API_KEY not set"
)
requires_anthropic = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"
)
requires_grok = pytest.mark.skipif(
    not os.environ.get("XAI_API_KEY"), reason="XAI_API_KEY not set"
)


BASIC_MESSAGES = [
    {"role": "system", "content": "Answer in exactly one word."},
    {"role": "user", "content": "What color is the sky on a clear day?"},
]


def _assert_text_result(r):
    """Common checks for a successful text call's LLMResult."""
    assert isinstance(r, LLMResult)
    assert isinstance(r.content, str) and len(r.content) > 0
    assert r.input_tokens > 0 and r.output_tokens > 0
    # latency is measured around every SDK call (defect 4)
    assert r.latency_ms is not None and r.latency_ms >= 0
    # text path exposes the pre-parse raw text
    assert r.raw_text is not None and len(r.raw_text) > 0
    # cache token fields always exist (>=0), even when unused
    assert r.cache_read_tokens >= 0 and r.cache_write_tokens >= 0


def _assert_structured_result(r):
    assert isinstance(r, LLMResult)
    assert isinstance(r.content, SimpleAnswer)
    assert len(r.content.answer) > 0
    assert r.input_tokens > 0 and r.output_tokens > 0
    assert r.latency_ms is not None and r.latency_ms >= 0


@requires_openai
class TestOpenAILive:
    def test_call_text(self):
        from skyward.llm.providers import OpenAIProvider
        _assert_text_result(OpenAIProvider().call(BASIC_MESSAGES, "gpt-4o-mini"))

    def test_call_structured(self):
        from skyward.llm.providers import OpenAIProvider
        _assert_structured_result(
            OpenAIProvider().call(BASIC_MESSAGES, "gpt-4o-mini", response_model=SimpleAnswer)
        )

    def test_reasoning_text_populated(self):
        """A reasoning model with a requested summary populates reasoning_text."""
        from skyward.llm.providers import OpenAIProvider
        r = OpenAIProvider().call(
            [{"role": "user", "content": "What is 2+2? Reason briefly, then answer."}],
            "gpt-5-mini",
            response_model=SimpleAnswer,
            reasoning={"summary": "detailed"},
        )
        assert isinstance(r.content, SimpleAnswer)
        assert r.reasoning_text is not None and len(r.reasoning_text) > 0

    def test_reasoning_model_text_with_max_tokens(self):
        """Reasoning model via text path + max_tokens must not error (uses
        max_completion_tokens under the hood)."""
        from skyward.llm.providers import OpenAIProvider
        from skyward.llm.providers import LLMResult
        r = OpenAIProvider().call(
            [{"role": "user", "content": "Reply with one word: ok"}],
            "gpt-5-mini",
            max_tokens=200,
        )
        assert isinstance(r, LLMResult)
        assert r.output_tokens > 0


@requires_gemini
class TestGeminiLive:
    def test_call_text(self):
        from skyward.llm.providers import GeminiProvider
        r = GeminiProvider().call(BASIC_MESSAGES, "gemini-2.5-flash")
        _assert_text_result(r)

    def test_call_structured(self):
        from skyward.llm.providers import GeminiProvider
        _assert_structured_result(
            GeminiProvider().call(BASIC_MESSAGES, "gemini-2.5-flash", response_model=SimpleAnswer)
        )


@requires_perplexity
class TestPerplexityLive:
    def test_call_text(self):
        from skyward.llm.providers import PerplexityProvider
        _assert_text_result(PerplexityProvider().call(BASIC_MESSAGES, "sonar"))

    def test_call_structured(self):
        from skyward.llm.providers import PerplexityProvider
        r = PerplexityProvider().call(BASIC_MESSAGES, "sonar", response_model=SimpleAnswer)
        assert isinstance(r, LLMResult)
        assert isinstance(r.content, SimpleAnswer)


@requires_anthropic
class TestAnthropicLive:
    def test_call_text(self):
        from skyward.llm.providers import AnthropicProvider
        _assert_text_result(AnthropicProvider().call(BASIC_MESSAGES, "claude-sonnet-4-20250514"))

    def test_call_structured(self):
        from skyward.llm.providers import AnthropicProvider
        _assert_structured_result(
            AnthropicProvider().call(
                BASIC_MESSAGES, "claude-sonnet-4-20250514", response_model=SimpleAnswer
            )
        )


@requires_grok
class TestGrokLive:
    def test_call_text(self):
        from skyward.llm.providers import GrokProvider
        _assert_text_result(GrokProvider().call(BASIC_MESSAGES, "grok-3-mini"))

    def test_call_structured(self):
        from skyward.llm.providers import GrokProvider
        r = GrokProvider().call(BASIC_MESSAGES, "grok-3-mini", response_model=SimpleAnswer)
        assert isinstance(r, LLMResult)
        assert isinstance(r.content, SimpleAnswer)


@requires_openai
class TestSessionLive:
    def test_multi_turn_session(self):
        from skyward.llm.providers import OpenAIProvider
        from skyward.llm.session import LLMSession

        p = OpenAIProvider()
        session = LLMSession(
            p,
            system_prompt="You are a helpful assistant. Keep answers under 20 words.",
            summarize_after_tokens=None,
        )

        # session.send() still returns the content (string), not LLMResult
        r1 = session.send("My name is Alice.", model="gpt-4o-mini")
        assert isinstance(r1, str)
        assert len(session.messages) == 2

        r2 = session.send("What is my name?", model="gpt-4o-mini")
        assert isinstance(r2, str)
        assert "alice" in r2.lower()
        assert len(session.messages) == 4
        # token tracking accumulated from LLMResult fields
        assert session.total_input_tokens > 0
        assert session.total_output_tokens > 0
