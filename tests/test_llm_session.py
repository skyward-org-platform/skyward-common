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
