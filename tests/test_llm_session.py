"""Tests for LLMSession wrapper."""

from pydantic import BaseModel


class SampleResponse(BaseModel):
    answer: str


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.next_response = ("default response", 100, 50)

    @property
    def name(self):
        return "fake"

    def call(self, messages, model, **kwargs):
        self.calls.append({"messages": list(messages), "model": model, **kwargs})
        return self.next_response


class TestSessionBasics:
    def test_session_wraps_provider(self):
        provider = FakeProvider()
        from skyward.llm.session import LLMSession

        session = LLMSession(provider, summarize_after_tokens=None)
        assert session.provider is provider

    def test_send_adds_user_and_assistant_to_history(self):
        from skyward.llm.session import LLMSession

        provider = FakeProvider()
        provider.next_response = ("hi back", 10, 5)
        session = LLMSession(provider, summarize_after_tokens=None)
        result = session.send("hello", model="test-model")
        assert result == "hi back"
        assert session.messages == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi back"},
        ]

    def test_send_structured_adds_serialized_response(self):
        from skyward.llm.session import LLMSession

        provider = FakeProvider()
        structured = SampleResponse(answer="42")
        provider.next_response = (structured, 20, 10)
        session = LLMSession(provider, summarize_after_tokens=None)
        result = session.send("what?", model="test-model", response_model=SampleResponse)
        assert result == structured
        assert session.messages[1]["content"] == '{"answer":"42"}'

    def test_system_prompt_set_in_constructor(self):
        from skyward.llm.session import LLMSession

        provider = FakeProvider()
        session = LLMSession(
            provider, system_prompt="You are helpful.", summarize_after_tokens=None
        )
        session.send("hi", model="m")
        call = provider.calls[0]
        assert call["messages"][0] == {"role": "system", "content": "You are helpful."}
        assert call["messages"][1] == {"role": "user", "content": "hi"}

    def test_multi_turn_conversation(self):
        from skyward.llm.session import LLMSession

        provider = FakeProvider()
        session = LLMSession(provider, summarize_after_tokens=None)
        provider.next_response = ("reply1", 10, 5)
        session.send("msg1", model="m")
        provider.next_response = ("reply2", 10, 5)
        session.send("msg2", model="m")
        # Second call should include full history
        second_call = provider.calls[1]
        assert second_call["messages"] == [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "reply1"},
            {"role": "user", "content": "msg2"},
        ]

    def test_token_tracking(self):
        from skyward.llm.session import LLMSession

        provider = FakeProvider()
        session = LLMSession(provider, summarize_after_tokens=None)
        provider.next_response = ("r1", 100, 50)
        session.send("a", model="m")
        provider.next_response = ("r2", 200, 80)
        session.send("b", model="m")
        assert session.total_input_tokens == 300
        assert session.total_output_tokens == 130

    def test_provider_kwargs_forwarded(self):
        from skyward.llm.session import LLMSession

        provider = FakeProvider()
        session = LLMSession(provider, summarize_after_tokens=None)
        session.send("hi", model="m", temperature=0.5, max_tokens=100)
        call = provider.calls[0]
        assert call["temperature"] == 0.5
        assert call["max_tokens"] == 100

    def test_clear_resets_history(self):
        from skyward.llm.session import LLMSession

        provider = FakeProvider()
        session = LLMSession(provider, summarize_after_tokens=None)
        provider.next_response = ("r", 100, 50)
        session.send("hi", model="m")
        session.clear()
        assert session.messages == []
        assert session.total_input_tokens == 0
        assert session.total_output_tokens == 0


class TestTokenSummarization:
    def test_no_summarization_when_disabled(self):
        from skyward.llm.session import LLMSession

        provider = FakeProvider()
        session = LLMSession(provider, summarize_after_tokens=None)
        provider.next_response = ("r", 100000, 50000)
        session.send("lots of tokens", model="m")
        assert len(session.messages) == 2

    def test_default_summarizer_uses_gemini_2_5_flash(self):
        """Default summarizer model should be gemini-2.5-flash, not deprecated 2.0."""
        from skyward.llm.session import LLMSession
        import inspect
        source = inspect.getsource(LLMSession._summarize)
        assert "gemini-2.5-flash" in source, "Default summarizer should use gemini-2.5-flash"
        assert "gemini-2.0-flash" not in source, "Should not reference deprecated gemini-2.0-flash"

    def test_summarization_triggers_after_token_threshold(self):
        from skyward.llm.session import LLMSession

        provider = FakeProvider()
        summarizer = FakeProvider()
        summarizer.next_response = ("[Summary of conversation so far]", 10, 5)

        session = LLMSession(
            provider,
            summarize_after_tokens=200,
            summarizer_provider=summarizer,
        )

        # First call: 150 total tokens — below threshold, no summarization
        provider.next_response = ("reply1", 100, 50)
        session.send("hello", model="m")
        assert len(session.messages) == 2  # user + assistant

        # Second call: 300 more tokens — total 450, above 200 threshold
        provider.next_response = ("reply2", 200, 100)
        session.send("world", model="m")

        # Summarization should have compressed messages to 1 summary message
        assert len(session.messages) == 1
        assert "[Summary" in session.messages[0]["content"]


class TestMessageSummarization:
    def test_summarization_triggers_after_message_count(self):
        from skyward.llm.session import LLMSession

        provider = FakeProvider()
        summarizer = FakeProvider()
        summarizer.next_response = ("Compressed history", 10, 5)

        session = LLMSession(
            provider,
            summarize_after_tokens=None,
            summarize_after_messages=4,
            summarizer_provider=summarizer,
        )

        provider.next_response = ("a", 10, 5)
        session.send("1", model="m")  # 2 messages
        assert len(session.messages) == 2

        provider.next_response = ("b", 10, 5)
        session.send("2", model="m")  # 4 messages — triggers
        assert len(session.messages) == 1
        assert "[Summary" in session.messages[0]["content"]


class TestCustomSummarization:
    def test_custom_function_called(self):
        from skyward.llm.session import LLMSession

        provider = FakeProvider()
        call_log = []

        def my_summarizer(messages):
            call_log.append(len(messages))
            return messages[-2:]

        session = LLMSession(
            provider,
            summarize_after_tokens=None,
            summarize_after_messages=4,
            summarize_fn=my_summarizer,
        )

        provider.next_response = ("a", 10, 5)
        session.send("1", model="m")

        provider.next_response = ("b", 10, 5)
        session.send("2", model="m")

        assert len(call_log) == 1
        assert call_log[0] == 4
        assert len(session.messages) == 2

    def test_custom_function_can_extract_and_replace(self):
        from skyward.llm.session import LLMSession

        provider = FakeProvider()
        extracted_data = []

        def extract_and_compress(messages):
            for msg in messages:
                if msg["role"] == "assistant":
                    extracted_data.append(msg["content"])
            return [{"role": "assistant", "content": "Prior context: discussed topics A and B"}]

        session = LLMSession(
            provider,
            summarize_after_tokens=None,
            summarize_after_messages=4,
            summarize_fn=extract_and_compress,
        )

        provider.next_response = ("fact A", 10, 5)
        session.send("tell me about A", model="m")

        provider.next_response = ("fact B", 10, 5)
        session.send("tell me about B", model="m")

        assert len(extracted_data) == 2
        assert "fact A" in extracted_data
        assert "fact B" in extracted_data
        assert len(session.messages) == 1
