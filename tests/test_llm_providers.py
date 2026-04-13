"""Tests for LLM provider base class and implementations."""
import pytest
from pydantic import BaseModel

from skyward.llm.providers import LLMProvider


class SampleResponse(BaseModel):
    answer: str
    confidence: float


class TestLLMProviderInterface:
    """Verify the base class enforces the correct interface."""

    def test_cannot_instantiate_abstract_class(self):
        with pytest.raises(TypeError):
            LLMProvider()

    def test_concrete_class_must_implement_call(self):
        class Incomplete(LLMProvider):
            @property
            def name(self):
                return "incomplete"
        with pytest.raises(TypeError):
            Incomplete()

    def test_concrete_class_with_call_can_be_instantiated(self):
        class Complete(LLMProvider):
            @property
            def name(self):
                return "complete"
            def call(self, messages, model, **kwargs):
                return ("hello", 10, 5)
        provider = Complete()
        assert provider.name == "complete"
        result, in_tok, out_tok = provider.call([], "test-model")
        assert result == "hello"
        assert in_tok == 10
        assert out_tok == 5
