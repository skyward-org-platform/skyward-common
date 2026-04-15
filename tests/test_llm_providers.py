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

    def test_structured_injects_schema_into_system(self):
        """Gemini should inject JSON schema into system_instruction for structured output."""
        with patch("skyward.llm.providers.genai") as mock_genai:
            import json
            provider, mock_client = self._make_provider(mock_genai)
            response_json = json.dumps({"answer": "yes", "confidence": 0.9})
            mock_client.models.generate_content.return_value = self._make_mock_response(text=response_json)
            provider.call(
                messages=[
                    {"role": "system", "content": "Be helpful."},
                    {"role": "user", "content": "question"},
                ],
                model="gemini-2.5-flash",
                response_model=SampleResponse,
            )
            call_kwargs = mock_client.models.generate_content.call_args[1]
            system_inst = call_kwargs["config"].system_instruction
            assert "JSON" in system_inst, f"Should contain JSON schema instruction, got: {system_inst}"
            assert "answer" in system_inst, "Should contain field names from schema"

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

    def test_structured_without_system_message(self):
        """Structured output should work even without a system message."""
        import json
        provider, mock_client = self._make_provider()
        usage = MockOpenAIUsage(10, 5)
        response_json = json.dumps({"answer": "4", "confidence": 1.0})
        mock_client.chat.completions.create.return_value = MockChatCompletion(response_json, usage)
        result, _, _ = provider.call(
            messages=[{"role": "user", "content": "What is 2+2?"}],
            model="sonar",
            response_model=SampleResponse,
        )
        assert isinstance(result, SampleResponse)
        # Verify a system message was prepended with the schema
        sent_msgs = mock_client.chat.completions.create.call_args[1]["messages"]
        assert sent_msgs[0]["role"] == "system"
        assert "JSON" in sent_msgs[0]["content"]


class TestAnthropicProvider:

    def _make_provider(self):
        from skyward.llm.providers import AnthropicProvider
        with patch("skyward.llm.providers.Anthropic") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            provider = AnthropicProvider(api_key="sk-ant-test")
        return provider, mock_client

    def test_name_property(self):
        provider, _ = self._make_provider()
        assert provider.name == "anthropic"

    def test_call_text_returns_string(self):
        provider, mock_client = self._make_provider()
        response = MagicMock()
        response.content = [MagicMock(text="hello")]
        response.usage.input_tokens = 120
        response.usage.output_tokens = 40
        mock_client.messages.create.return_value = response
        result, in_tok, out_tok = provider.call(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-sonnet-4-20250514",
        )
        assert result == "hello"
        assert in_tok == 120
        assert out_tok == 40

    def test_call_text_with_system_message(self):
        provider, mock_client = self._make_provider()
        response = MagicMock()
        response.content = [MagicMock(text="hi there")]
        response.usage.input_tokens = 50
        response.usage.output_tokens = 10
        mock_client.messages.create.return_value = response
        provider.call(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hello"},
            ],
            model="claude-sonnet-4-20250514",
        )
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["system"] == "You are helpful."
        # system message should NOT be in the messages list
        for msg in call_kwargs["messages"]:
            assert msg["role"] != "system"

    def test_call_structured_returns_pydantic_model(self):
        provider, mock_client = self._make_provider()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="tool_use", input={"answer": "yes", "confidence": 0.95})]
        mock_response.usage.input_tokens = 200
        mock_response.usage.output_tokens = 100
        mock_client.messages.create.return_value = mock_response
        result, in_tok, out_tok = provider.call(
            messages=[{"role": "user", "content": "question"}],
            model="claude-sonnet-4-20250514",
            response_model=SampleResponse,
        )
        assert isinstance(result, SampleResponse)
        assert result.answer == "yes"
        assert in_tok == 200
        assert out_tok == 100

    def test_call_structured_uses_tools(self):
        """Anthropic structured output uses tools with tool_choice, not messages.parse."""
        provider, mock_client = self._make_provider()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="tool_use", input={"answer": "yes", "confidence": 0.95})]
        mock_response.usage.input_tokens = 200
        mock_response.usage.output_tokens = 100
        mock_client.messages.create.return_value = mock_response
        result, in_tok, out_tok = provider.call(
            messages=[{"role": "user", "content": "question"}],
            model="claude-sonnet-4-20250514",
            response_model=SampleResponse,
        )
        call_kwargs = mock_client.messages.create.call_args[1]
        assert "tools" in call_kwargs, f"Expected tools, got keys: {list(call_kwargs.keys())}"
        assert call_kwargs["tool_choice"]["type"] == "tool"
        assert isinstance(result, SampleResponse)
        assert result.answer == "yes"

    def test_provider_kwargs_forwarded(self):
        provider, mock_client = self._make_provider()
        response = MagicMock()
        response.content = [MagicMock(text="ok")]
        response.usage.input_tokens = 10
        response.usage.output_tokens = 5
        mock_client.messages.create.return_value = response
        provider.call(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-sonnet-4-20250514",
            thinking={"type": "enabled", "budget_tokens": 10000},
        )
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["thinking"] == {"type": "enabled", "budget_tokens": 10000}

    def test_init_with_env_var_fallback(self):
        from skyward.llm.providers import AnthropicProvider
        with patch("skyward.llm.providers.Anthropic") as mock_cls, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-env-key"}, clear=True):
            mock_cls.return_value = MagicMock()
            provider = AnthropicProvider()
            mock_cls.assert_called_once_with(api_key="sk-ant-env-key")

    def test_init_no_key_raises(self):
        from skyward.llm.providers import AnthropicProvider
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API key"):
                AnthropicProvider()

    def test_max_tokens_defaults_to_4096(self):
        provider, mock_client = self._make_provider()
        response = MagicMock()
        response.content = [MagicMock(text="ok")]
        response.usage.input_tokens = 10
        response.usage.output_tokens = 5
        mock_client.messages.create.return_value = response
        provider.call(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-sonnet-4-20250514",
        )
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["max_tokens"] == 4096

    def test_retries_on_transient_error(self):
        provider, mock_client = self._make_provider()
        response = MagicMock()
        response.content = [MagicMock(text="recovered")]
        response.usage.input_tokens = 10
        response.usage.output_tokens = 5
        mock_client.messages.create.side_effect = [
            ConnectionError("transient"),
            response,
        ]
        result, in_tok, out_tok = provider.call(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-sonnet-4-20250514",
            max_retries=2,
            retry_delay=0,
        )
        assert result == "recovered"
        assert mock_client.messages.create.call_count == 2

    def test_raises_after_max_retries_exhausted(self):
        provider, mock_client = self._make_provider()
        mock_client.messages.create.side_effect = ConnectionError("down")
        with pytest.raises(RuntimeError, match="failed after 2 attempts"):
            provider.call(
                messages=[{"role": "user", "content": "hi"}],
                model="claude-sonnet-4-20250514",
                max_retries=2,
                retry_delay=0,
            )


class TestGrokProvider:

    def _make_provider(self):
        from skyward.llm.providers import GrokProvider
        with patch("skyward.llm.providers.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            provider = GrokProvider(api_key="xai-test-key")
        return provider, mock_client

    def test_name_property(self):
        provider, _ = self._make_provider()
        assert provider.name == "grok"

    def test_call_text_returns_string(self):
        provider, mock_client = self._make_provider()
        usage = MockOpenAIUsage(100, 50)
        mock_client.chat.completions.create.return_value = MockChatCompletion("hello world", usage)
        result, in_tok, out_tok = provider.call(
            messages=[{"role": "user", "content": "hi"}],
            model="grok-3",
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
            model="grok-3",
            response_model=SampleResponse,
        )
        assert isinstance(result, SampleResponse)
        assert result.answer == "yes"
        assert result.confidence == 0.95
        assert in_tok == 200
        assert out_tok == 100

    def test_structured_injects_json_schema_into_system(self):
        """Grok should inject JSON schema into system message for structured output."""
        import json
        provider, mock_client = self._make_provider()
        usage = MockOpenAIUsage(10, 5)
        response_json = json.dumps({"answer": "yes", "confidence": 0.9})
        mock_client.chat.completions.create.return_value = MockChatCompletion(response_json, usage)
        provider.call(
            messages=[
                {"role": "system", "content": "Be helpful."},
                {"role": "user", "content": "question"},
            ],
            model="grok-3",
            response_model=SampleResponse,
        )
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        system_msg = [m for m in call_kwargs["messages"] if m["role"] == "system"][0]
        assert "JSON" in system_msg["content"], "System message should contain JSON schema instruction"
        assert "answer" in system_msg["content"], "System message should contain field names from schema"

    def test_uses_xai_base_url(self):
        from skyward.llm.providers import GrokProvider
        with patch("skyward.llm.providers.OpenAI") as mock_openai_cls:
            mock_openai_cls.return_value = MagicMock()
            provider = GrokProvider(api_key="xai-test-key")
            mock_openai_cls.assert_called_once_with(
                api_key="xai-test-key",
                base_url="https://api.x.ai/v1",
            )

    def test_init_with_env_var_fallback(self):
        from skyward.llm.providers import GrokProvider
        with patch("skyward.llm.providers.OpenAI") as mock_openai_cls, \
             patch.dict("os.environ", {"XAI_API_KEY": "xai-env-key"}, clear=True):
            mock_openai_cls.return_value = MagicMock()
            provider = GrokProvider()
            mock_openai_cls.assert_called_once_with(
                api_key="xai-env-key",
                base_url="https://api.x.ai/v1",
            )

    def test_init_no_key_raises(self):
        from skyward.llm.providers import GrokProvider
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="API key"):
                GrokProvider()

    def test_provider_kwargs_forwarded(self):
        provider, mock_client = self._make_provider()
        usage = MockOpenAIUsage(10, 5)
        mock_client.chat.completions.create.return_value = MockChatCompletion("ok", usage)
        provider.call(
            messages=[{"role": "user", "content": "hi"}],
            model="grok-3",
            top_p=0.9,
            seed=42,
        )
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["top_p"] == 0.9
        assert call_kwargs["seed"] == 42

    def test_retries_on_transient_error(self):
        provider, mock_client = self._make_provider()
        usage = MockOpenAIUsage(10, 5)
        mock_client.chat.completions.create.side_effect = [
            ConnectionError("transient"),
            MockChatCompletion("recovered", usage),
        ]
        result, in_tok, out_tok = provider.call(
            messages=[{"role": "user", "content": "hi"}],
            model="grok-3",
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
                model="grok-3",
                max_retries=2,
                retry_delay=0,
            )


class TestGetProvider:
    """Tests for the get_provider() factory function."""

    def test_get_openai_with_api_key(self):
        from skyward.llm.providers import get_provider, OpenAIProvider
        with patch("skyward.llm.providers.OpenAI"):
            provider = get_provider("openai", api_key="sk-test")
        assert isinstance(provider, OpenAIProvider)

    def test_get_gemini_with_api_key(self):
        from skyward.llm.providers import get_provider, GeminiProvider
        with patch("skyward.llm.providers.genai"):
            provider = get_provider("gemini", api_key="gemini-test")
        assert isinstance(provider, GeminiProvider)

    def test_get_perplexity_with_api_key(self):
        from skyward.llm.providers import get_provider, PerplexityProvider
        with patch("skyward.llm.providers.OpenAI"):
            provider = get_provider("perplexity", api_key="pplx-test")
        assert isinstance(provider, PerplexityProvider)

    def test_get_anthropic_with_api_key(self):
        from skyward.llm.providers import get_provider, AnthropicProvider
        with patch("skyward.llm.providers.Anthropic"):
            provider = get_provider("anthropic", api_key="sk-ant-test")
        assert isinstance(provider, AnthropicProvider)

    def test_get_grok_with_api_key(self):
        from skyward.llm.providers import get_provider, GrokProvider
        with patch("skyward.llm.providers.OpenAI"):
            provider = get_provider("grok", api_key="xai-test")
        assert isinstance(provider, GrokProvider)

    def test_unknown_provider_raises(self):
        from skyward.llm.providers import get_provider
        with pytest.raises(ValueError, match="Unknown provider"):
            get_provider("unknown_provider", api_key="key")

    def test_get_openai_with_client_object(self):
        from skyward.llm.providers import get_provider, OpenAIProvider
        mock_client = MagicMock()
        provider = get_provider("openai", openai_client=mock_client)
        assert isinstance(provider, OpenAIProvider)

    def test_get_provider_falls_back_to_env_var(self):
        """get_provider() with no api_key should let constructor use env var."""
        from skyward.llm.providers import get_provider, OpenAIProvider
        with patch("skyward.llm.providers.OpenAI") as mock_cls, \
             patch.dict("os.environ", {"OPENAI_API_KEY": "sk-from-env"}):
            mock_cls.return_value = MagicMock()
            provider = get_provider("openai")
            assert isinstance(provider, OpenAIProvider)


class TestModelMappings:
    """Tests for model name mapping dicts."""

    def test_anthropic_models_exist(self):
        from skyward.llm.providers import ANTHROPIC_MODELS
        assert "claude-opus-4-20250514" in ANTHROPIC_MODELS
        assert "claude-sonnet-4-20250514" in ANTHROPIC_MODELS
        assert "claude-haiku-3-5-20241022" in ANTHROPIC_MODELS

    def test_grok_models_exist(self):
        from skyward.llm.providers import GROK_MODELS
        assert "grok-3" in GROK_MODELS
        assert "grok-3-mini" in GROK_MODELS
        assert "grok-3-fast" in GROK_MODELS

    def test_gemini_deprecated_models_removed(self):
        from skyward.llm.providers import GEMINI_MODELS
        assert "gemini-2.0-flash" not in GEMINI_MODELS, "gemini-2.0-flash is deprecated"
        assert "gemini-1.5-pro" not in GEMINI_MODELS, "gemini-1.5-pro is deprecated"
        assert "gemini-1.5-flash" not in GEMINI_MODELS, "gemini-1.5-flash is deprecated"
        # Current models should be present
        assert "gemini-2.5-flash" in GEMINI_MODELS
        assert "gemini-2.5-pro" in GEMINI_MODELS


class TestBackwardsCompatibility:
    """Ensure call_structured and call_text still work."""

    def test_call_structured_delegates_to_call(self):
        from skyward.llm.providers import OpenAIProvider
        mock_client = MagicMock()
        provider = OpenAIProvider(client=mock_client)

        parsed = SampleResponse(answer="yes", confidence=0.9)
        usage = MockOpenAIUsage(100, 50)
        mock_client.responses.parse.return_value = MockResponsesParsed(parsed, usage)

        result, in_tok, out_tok = provider.call_structured(
            messages=[{"role": "user", "content": "q"}],
            response_model=SampleResponse,
            model="gpt-4o",
        )
        assert isinstance(result, SampleResponse)

    def test_call_text_delegates_to_call(self):
        from skyward.llm.providers import OpenAIProvider
        mock_client = MagicMock()
        provider = OpenAIProvider(client=mock_client)

        usage = MockOpenAIUsage(100, 50)
        mock_client.chat.completions.create.return_value = MockChatCompletion("hello", usage)

        result, in_tok, out_tok = provider.call_text(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o",
        )
        assert result == "hello"


class TestLLMExports:
    """Tests that __init__.py exports new providers and mappings."""

    def test_anthropic_provider_exported(self):
        from skyward.llm import AnthropicProvider
        assert AnthropicProvider is not None

    def test_grok_provider_exported(self):
        from skyward.llm import GrokProvider
        assert GrokProvider is not None

    def test_anthropic_models_exported(self):
        from skyward.llm import ANTHROPIC_MODELS
        assert "claude-sonnet-4-20250514" in ANTHROPIC_MODELS

    def test_grok_models_exported(self):
        from skyward.llm import GROK_MODELS
        assert "grok-3" in GROK_MODELS

    def test_anthropic_costs_exported(self):
        from skyward.llm import ANTHROPIC_COSTS
        assert "claude-sonnet-4-20250514" in ANTHROPIC_COSTS

    def test_grok_costs_exported(self):
        from skyward.llm import GROK_COSTS
        assert "grok-3" in GROK_COSTS

    def test_llm_session_importable(self):
        from skyward.llm import LLMSession
        assert LLMSession is not None

    def test_llm_session_in_all(self):
        import skyward.llm as llm_module
        assert "LLMSession" in llm_module.__all__
