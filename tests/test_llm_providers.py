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


class TestGeminiProvider:

    def _make_mock_response(self, text="hello", prompt_tokens=100, completion_tokens=50):
        """Create a mock Gemini response object."""
        response = MagicMock()
        response.text = text
        response.usage_metadata.prompt_token_count = prompt_tokens
        response.usage_metadata.candidates_token_count = completion_tokens
        return response

    def _make_provider(self, mock_genai):
        """Create a GeminiProvider with a mocked genai module."""
        from skyward.llm.providers import GeminiProvider
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        provider = GeminiProvider(api_key="test-gemini-key")
        return provider, mock_client

    def test_name_property(self):
        from skyward.llm.providers import GeminiProvider
        with patch("skyward.llm.providers.genai"):
            provider = GeminiProvider(api_key="test-key")
        assert provider.name == "gemini"

    def test_call_text_returns_string(self):
        with patch("skyward.llm.providers.genai") as mock_genai:
            provider, mock_client = self._make_provider(mock_genai)
            mock_client.models.generate_content.return_value = self._make_mock_response(
                text="hello world", prompt_tokens=100, completion_tokens=50,
            )
            result, in_tok, out_tok = provider.call(
                messages=[{"role": "user", "content": "hi"}],
                model="gemini-2.0-flash",
            )
            assert result == "hello world"
            assert in_tok == 100
            assert out_tok == 50

    def test_call_structured_returns_pydantic_model(self):
        import json
        with patch("skyward.llm.providers.genai") as mock_genai:
            provider, mock_client = self._make_provider(mock_genai)
            response_json = json.dumps({"answer": "yes", "confidence": 0.95})
            mock_client.models.generate_content.return_value = self._make_mock_response(
                text=response_json, prompt_tokens=200, completion_tokens=100,
            )
            result, in_tok, out_tok = provider.call(
                messages=[{"role": "user", "content": "question"}],
                model="gemini-2.0-flash",
                response_model=SampleResponse,
            )
            assert isinstance(result, SampleResponse)
            assert result.answer == "yes"
            assert result.confidence == 0.95
            assert in_tok == 200
            assert out_tok == 100

    def test_client_created_once_in_init(self):
        with patch("skyward.llm.providers.genai") as mock_genai:
            provider, mock_client = self._make_provider(mock_genai)
            # Client created once during __init__
            assert mock_genai.Client.call_count == 1
            # Make two calls
            mock_client.models.generate_content.return_value = self._make_mock_response()
            provider.call(
                messages=[{"role": "user", "content": "hi"}],
                model="gemini-2.0-flash",
            )
            provider.call(
                messages=[{"role": "user", "content": "bye"}],
                model="gemini-2.0-flash",
            )
            # Client still only created once (not per call)
            assert mock_genai.Client.call_count == 1

    def test_google_api_key_popped_during_init(self):
        """GOOGLE_API_KEY is temporarily removed during __init__ then restored."""
        from skyward.llm.providers import GeminiProvider
        captured_env = {}

        def capture_env(**kwargs):
            captured_env["GOOGLE_API_KEY"] = os.environ.get("GOOGLE_API_KEY")
            return MagicMock()

        with patch("skyward.llm.providers.genai") as mock_genai, \
             patch.dict("os.environ", {"GOOGLE_API_KEY": "some-google-key"}):
            mock_genai.Client.side_effect = capture_env
            provider = GeminiProvider(api_key="gemini-key")
            # During Client creation, GOOGLE_API_KEY should have been absent
            assert captured_env["GOOGLE_API_KEY"] is None
            # After __init__, GOOGLE_API_KEY should be restored
            assert os.environ["GOOGLE_API_KEY"] == "some-google-key"

    def test_init_with_env_var_fallback(self):
        from skyward.llm.providers import GeminiProvider
        with patch("skyward.llm.providers.genai") as mock_genai, \
             patch.dict("os.environ", {"GEMINI_API_KEY": "env-gemini-key"}, clear=True):
            provider = GeminiProvider()
            mock_genai.Client.assert_called_once_with(api_key="env-gemini-key")

    def test_init_no_key_raises(self):
        from skyward.llm.providers import GeminiProvider
        with patch("skyward.llm.providers.genai"), \
             patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API key"):
                GeminiProvider()

    def test_system_message_converted(self):
        """System messages become system_instruction, not contents."""
        with patch("skyward.llm.providers.genai") as mock_genai:
            provider, mock_client = self._make_provider(mock_genai)
            mock_client.models.generate_content.return_value = self._make_mock_response()
            provider.call(
                messages=[
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "hi"},
                ],
                model="gemini-2.0-flash",
            )
            call_kwargs = mock_client.models.generate_content.call_args[1]
            # System message should NOT appear in contents
            contents = call_kwargs["contents"]
            for c in contents:
                assert c["role"] != "system"
            # System message should be in config's system_instruction
            config = call_kwargs["config"]
            assert config.system_instruction == "You are helpful."

    def test_retries_on_transient_error(self):
        with patch("skyward.llm.providers.genai") as mock_genai:
            provider, mock_client = self._make_provider(mock_genai)
            mock_client.models.generate_content.side_effect = [
                ConnectionError("transient"),
                self._make_mock_response(text="recovered"),
            ]
            result, in_tok, out_tok = provider.call(
                messages=[{"role": "user", "content": "hi"}],
                model="gemini-2.0-flash",
                max_retries=2,
                retry_delay=0,
            )
            assert result == "recovered"
            assert mock_client.models.generate_content.call_count == 2

    def test_raises_after_max_retries_exhausted(self):
        with patch("skyward.llm.providers.genai") as mock_genai:
            provider, mock_client = self._make_provider(mock_genai)
            mock_client.models.generate_content.side_effect = ConnectionError("down")
            with pytest.raises(RuntimeError, match="failed after 2 attempts"):
                provider.call(
                    messages=[{"role": "user", "content": "hi"}],
                    model="gemini-2.0-flash",
                    max_retries=2,
                    retry_delay=0,
                )

    def test_no_legacy_call_structured_method(self):
        """GeminiProvider should not define its own call_structured — only call()."""
        from skyward.llm.providers import GeminiProvider
        assert "call_structured" not in GeminiProvider.__dict__

    def test_no_legacy_call_text_method(self):
        """GeminiProvider should not define its own call_text — only call()."""
        from skyward.llm.providers import GeminiProvider
        assert "call_text" not in GeminiProvider.__dict__


class TestPerplexityProvider:

    def _make_provider(self):
        from skyward.llm.providers import PerplexityProvider
        with patch("skyward.llm.providers.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            provider = PerplexityProvider(api_key="pplx-test-key")
        return provider, mock_client

    def test_name_property(self):
        provider, _ = self._make_provider()
        assert provider.name == "perplexity"

    def test_call_text_returns_string(self):
        provider, mock_client = self._make_provider()
        usage = MockOpenAIUsage(100, 50)
        mock_client.chat.completions.create.return_value = MockChatCompletion("hello world", usage)
        result, in_tok, out_tok = provider.call(
            messages=[{"role": "user", "content": "hi"}],
            model="sonar",
        )
        assert result == "hello world"
        assert in_tok == 100
        assert out_tok == 50

    def test_call_structured_returns_pydantic_model(self):
        import json
        provider, mock_client = self._make_provider()
        usage = MockOpenAIUsage(200, 100)
        response_json = json.dumps({"answer": "yes", "confidence": 0.95})
        mock_client.chat.completions.create.return_value = MockChatCompletion(response_json, usage)
        result, in_tok, out_tok = provider.call(
            messages=[
                {"role": "system", "content": "Be helpful."},
                {"role": "user", "content": "question"},
            ],
            model="sonar",
            response_model=SampleResponse,
        )
        assert isinstance(result, SampleResponse)
        assert result.answer == "yes"
        assert result.confidence == 0.95
        assert in_tok == 200
        assert out_tok == 100

    def test_tools_kwarg_filtered_out(self):
        provider, mock_client = self._make_provider()
        usage = MockOpenAIUsage(10, 5)
        mock_client.chat.completions.create.return_value = MockChatCompletion("ok", usage)
        provider.call(
            messages=[{"role": "user", "content": "hi"}],
            model="sonar",
            tools=[{"type": "function", "function": {"name": "search"}}],
        )
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert "tools" not in call_kwargs

    def test_init_with_env_var_fallback(self):
        from skyward.llm.providers import PerplexityProvider
        with patch("skyward.llm.providers.OpenAI") as mock_openai_cls, \
             patch.dict("os.environ", {"PERPLEXITY_API_KEY": "pplx-env-key"}, clear=True):
            mock_openai_cls.return_value = MagicMock()
            provider = PerplexityProvider()
            mock_openai_cls.assert_called_once_with(
                api_key="pplx-env-key",
                base_url="https://api.perplexity.ai",
            )

    def test_init_no_key_raises(self):
        from skyward.llm.providers import PerplexityProvider
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API key"):
                PerplexityProvider()

    def test_retries_on_transient_error(self):
        provider, mock_client = self._make_provider()
        usage = MockOpenAIUsage(10, 5)
        mock_client.chat.completions.create.side_effect = [
            ConnectionError("transient"),
            MockChatCompletion("recovered", usage),
        ]
        result, in_tok, out_tok = provider.call(
            messages=[{"role": "user", "content": "hi"}],
            model="sonar",
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
                model="sonar",
                max_retries=2,
                retry_delay=0,
            )

    def test_no_legacy_call_structured_method(self):
        """PerplexityProvider should not define its own call_structured — only call()."""
        from skyward.llm.providers import PerplexityProvider
        assert "call_structured" not in PerplexityProvider.__dict__

    def test_no_legacy_call_text_method(self):
        """PerplexityProvider should not define its own call_text — only call()."""
        from skyward.llm.providers import PerplexityProvider
        assert "call_text" not in PerplexityProvider.__dict__
