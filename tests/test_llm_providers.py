"""Tests for LLM provider base class and implementations."""
import os
import pytest
from unittest.mock import MagicMock, patch

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


class MockOpenAIUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.prompt_tokens = input_tokens
        self.completion_tokens = output_tokens


class MockResponsesParsed:
    def __init__(self, parsed, usage):
        self.output_parsed = parsed
        self.usage = usage


class MockChatCompletion:
    def __init__(self, content, usage):
        self.choices = [MagicMock(message=MagicMock(content=content))]
        self.usage = usage


class TestOpenAIProvider:

    def _make_provider(self):
        from skyward.llm.providers import OpenAIProvider
        mock_client = MagicMock()
        return OpenAIProvider(client=mock_client), mock_client

    def test_name_property(self):
        provider, _ = self._make_provider()
        assert provider.name == "openai"

    def test_call_text_returns_string(self):
        provider, mock_client = self._make_provider()
        usage = MockOpenAIUsage(100, 50)
        mock_client.chat.completions.create.return_value = MockChatCompletion("hello world", usage)
        result, in_tok, out_tok = provider.call(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o",
        )
        assert result == "hello world"
        assert in_tok == 100
        assert out_tok == 50

    def test_call_structured_returns_pydantic_model(self):
        provider, mock_client = self._make_provider()
        parsed = SampleResponse(answer="yes", confidence=0.95)
        usage = MockOpenAIUsage(200, 100)
        mock_client.responses.parse.return_value = MockResponsesParsed(parsed, usage)
        result, in_tok, out_tok = provider.call(
            messages=[{"role": "user", "content": "question"}],
            model="gpt-4o",
            response_model=SampleResponse,
        )
        assert isinstance(result, SampleResponse)
        assert result.answer == "yes"
        assert in_tok == 200
        assert out_tok == 100

    def test_provider_kwargs_forwarded_to_text(self):
        provider, mock_client = self._make_provider()
        usage = MockOpenAIUsage(10, 5)
        mock_client.chat.completions.create.return_value = MockChatCompletion("ok", usage)
        provider.call(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o",
            top_p=0.9,
            seed=42,
        )
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["top_p"] == 0.9
        assert call_kwargs["seed"] == 42

    def test_provider_kwargs_forwarded_to_structured(self):
        provider, mock_client = self._make_provider()
        parsed = SampleResponse(answer="ok", confidence=0.5)
        usage = MockOpenAIUsage(10, 5)
        mock_client.responses.parse.return_value = MockResponsesParsed(parsed, usage)
        provider.call(
            messages=[{"role": "user", "content": "q"}],
            model="gpt-4o",
            response_model=SampleResponse,
            store=True,
        )
        call_kwargs = mock_client.responses.parse.call_args[1]
        assert call_kwargs["store"] is True

    def test_temperature_excluded_for_reasoning_models(self):
        provider, mock_client = self._make_provider()
        usage = MockOpenAIUsage(10, 5)
        mock_client.chat.completions.create.return_value = MockChatCompletion("ok", usage)
        provider.call(
            messages=[{"role": "user", "content": "hi"}],
            model="o1",
            temperature=0.7,
        )
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert "temperature" not in call_kwargs

    def test_init_with_api_key_creates_client(self):
        from skyward.llm.providers import OpenAIProvider
        with patch("skyward.llm.providers.OpenAI") as mock_openai_cls:
            mock_openai_cls.return_value = MagicMock()
            provider = OpenAIProvider(api_key="sk-test-123")
            mock_openai_cls.assert_called_once_with(api_key="sk-test-123")

    def test_init_with_env_var_fallback(self):
        from skyward.llm.providers import OpenAIProvider
        with patch("skyward.llm.providers.OpenAI") as mock_openai_cls, \
             patch.dict("os.environ", {"OPENAI_API_KEY": "sk-env-key"}):
            mock_openai_cls.return_value = MagicMock()
            provider = OpenAIProvider()
            mock_openai_cls.assert_called_once_with(api_key="sk-env-key")

    def test_init_no_key_raises(self):
        from skyward.llm.providers import OpenAIProvider
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API key"):
                OpenAIProvider()

    def test_retries_on_transient_error(self):
        provider, mock_client = self._make_provider()
        usage = MockOpenAIUsage(10, 5)
        mock_client.chat.completions.create.side_effect = [
            ConnectionError("transient"),
            MockChatCompletion("recovered", usage),
        ]
        result, in_tok, out_tok = provider.call(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o",
            max_retries=2,
            retry_delay=0,
        )
        assert result == "recovered"
        assert mock_client.chat.completions.create.call_count == 2

    def test_raises_after_max_retries_exhausted(self):
        provider, mock_client = self._make_provider()
        mock_client.chat.completions.create.side_effect = ConnectionError("down")
        with pytest.raises(RuntimeError, match="failed after 2 attempts"):
            provider.call(
                messages=[{"role": "user", "content": "hi"}],
                model="gpt-4o",
                max_retries=2,
                retry_delay=0,
            )
