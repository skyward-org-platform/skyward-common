"""Stateful LLM session wrapper with automatic summarization."""

from __future__ import annotations

from pydantic import BaseModel


class LLMSession:
    def __init__(self, provider, *, system_prompt=None, summarize_after_tokens=None):
        self.provider = provider
        self.system_prompt = system_prompt
        self._messages = []
        self._total_input_tokens = 0
        self._total_output_tokens = 0

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
        return result

    def clear(self):
        self._messages.clear()
        self._total_input_tokens = 0
        self._total_output_tokens = 0
