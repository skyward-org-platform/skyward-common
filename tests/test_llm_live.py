"""Live integration tests for LLM providers.

Skipped unless the corresponding API key env var is set.
Run with: uv run python -m pytest tests/test_llm_live.py -v
"""
import os
import pytest
from pydantic import BaseModel


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


@requires_openai
class TestOpenAILive:
    def test_call_text(self):
        from skyward.llm.providers import OpenAIProvider
        p = OpenAIProvider()
        result, in_tok, out_tok = p.call(BASIC_MESSAGES, "gpt-4o-mini")
        assert isinstance(result, str)
        assert len(result) > 0
        assert in_tok > 0 and out_tok > 0

    def test_call_structured(self):
        from skyward.llm.providers import OpenAIProvider
        p = OpenAIProvider()
        result, in_tok, out_tok = p.call(
            BASIC_MESSAGES, "gpt-4o-mini", response_model=SimpleAnswer,
        )
        assert isinstance(result, SimpleAnswer)
        assert len(result.answer) > 0


@requires_gemini
class TestGeminiLive:
    def test_call_text(self):
        from skyward.llm.providers import GeminiProvider
        p = GeminiProvider()
        result, in_tok, out_tok = p.call(BASIC_MESSAGES, "gemini-2.0-flash")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_call_structured(self):
        from skyward.llm.providers import GeminiProvider
        p = GeminiProvider()
        result, in_tok, out_tok = p.call(
            BASIC_MESSAGES, "gemini-2.0-flash", response_model=SimpleAnswer,
        )
        assert isinstance(result, SimpleAnswer)
        assert len(result.answer) > 0


@requires_perplexity
class TestPerplexityLive:
    def test_call_text(self):
        from skyward.llm.providers import PerplexityProvider
        p = PerplexityProvider()
        result, in_tok, out_tok = p.call(BASIC_MESSAGES, "sonar")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_call_structured(self):
        from skyward.llm.providers import PerplexityProvider
        p = PerplexityProvider()
        result, in_tok, out_tok = p.call(
            BASIC_MESSAGES, "sonar", response_model=SimpleAnswer,
        )
        assert isinstance(result, SimpleAnswer)


@requires_anthropic
class TestAnthropicLive:
    def test_call_text(self):
        from skyward.llm.providers import AnthropicProvider
        p = AnthropicProvider()
        result, in_tok, out_tok = p.call(BASIC_MESSAGES, "claude-sonnet-4-20250514")
        assert isinstance(result, str)
        assert len(result) > 0
        assert in_tok > 0 and out_tok > 0

    def test_call_structured(self):
        from skyward.llm.providers import AnthropicProvider
        p = AnthropicProvider()
        result, in_tok, out_tok = p.call(
            BASIC_MESSAGES, "claude-sonnet-4-20250514", response_model=SimpleAnswer,
        )
        assert isinstance(result, SimpleAnswer)
        assert len(result.answer) > 0


@requires_grok
class TestGrokLive:
    def test_call_text(self):
        from skyward.llm.providers import GrokProvider
        p = GrokProvider()
        result, in_tok, out_tok = p.call(BASIC_MESSAGES, "grok-3-mini")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_call_structured(self):
        from skyward.llm.providers import GrokProvider
        p = GrokProvider()
        result, in_tok, out_tok = p.call(
            BASIC_MESSAGES, "grok-3-mini", response_model=SimpleAnswer,
        )
        assert isinstance(result, SimpleAnswer)


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

        r1 = session.send("My name is Alice.", model="gpt-4o-mini")
        assert isinstance(r1, str)
        assert len(session.messages) == 2

        r2 = session.send("What is my name?", model="gpt-4o-mini")
        assert isinstance(r2, str)
        assert "alice" in r2.lower()
        assert len(session.messages) == 4
