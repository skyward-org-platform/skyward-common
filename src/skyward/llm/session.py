"""Stateful LLM session wrapper with automatic summarization."""

from __future__ import annotations

from pydantic import BaseModel


class LLMSession:
    def __init__(self, provider, *, system_prompt=None, summarize_after_tokens=None,
                 summarize_after_messages=None, summarize_fn=None, summarizer_provider=None):
        self.provider = provider
        self.system_prompt = system_prompt
        self._messages = []
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._summarize_after_tokens = summarize_after_tokens
        self._summarize_after_messages = summarize_after_messages
        self._summarize_fn = summarize_fn
        self._summarizer_provider = summarizer_provider

    @property
    def total_input_tokens(self):
        return self._total_input_tokens

    @property
    def total_output_tokens(self):
        return self._total_output_tokens

    @property
    def messages(self):
        return list(self._messages)

    def send(self, content, model, **kwargs):
        self._messages.append({"role": "user", "content": content})
        call_messages = []
        if self.system_prompt:
            call_messages.append({"role": "system", "content": self.system_prompt})
        call_messages.extend(self._messages)
        result, in_tok, out_tok = self.provider.call(
            call_messages, model, **kwargs
        )
        self._total_input_tokens += in_tok
        self._total_output_tokens += out_tok
        if isinstance(result, BaseModel):
            assistant_content = result.model_dump_json()
        else:
            assistant_content = result
        self._messages.append({"role": "assistant", "content": assistant_content})
        self._maybe_summarize()
        return result

    def clear(self):
        self._messages.clear()
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    def _maybe_summarize(self):
        should_summarize = False
        if self._summarize_after_tokens is not None:
            total = self._total_input_tokens + self._total_output_tokens
            if total >= self._summarize_after_tokens:
                should_summarize = True
        if self._summarize_after_messages is not None:
            if len(self._messages) >= self._summarize_after_messages:
                should_summarize = True
        if should_summarize and len(self._messages) > 2:
            self._summarize()

    def _summarize(self):
        if self._summarize_fn is not None:
            self._messages[:] = self._summarize_fn(self._messages)
            return
        summarizer = self._summarizer_provider
        if summarizer is None:
            from skyward.llm.providers import GeminiProvider
            summarizer = GeminiProvider()
        conversation_text = "\n".join(
            f"{msg['role'].upper()}: {msg['content']}" for msg in self._messages
        )
        summary_result, _, _ = summarizer.call(
            messages=[
                {"role": "system", "content": (
                    "Summarize the following conversation concisely. "
                    "Preserve all key facts, decisions, and context needed "
                    "to continue the conversation. Output only the summary."
                )},
                {"role": "user", "content": conversation_text},
            ],
            model="gemini-2.0-flash",
        )
        self._messages[:] = [
            {"role": "assistant", "content": f"[Summary of prior conversation]\n{summary_result}"}
        ]
