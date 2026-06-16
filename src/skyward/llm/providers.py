"""
Multi-model LLM provider abstraction.

Supports:
- OpenAI (gpt-4o, gpt-4o-mini, gpt-4.5-preview, gpt-5 family)
- Google Gemini (gemini-2.0-flash, gemini-1.5-pro, gemini-3 family)
- Perplexity (sonar, sonar-pro, sonar-reasoning-pro)
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar

from anthropic import Anthropic
from google import genai
from google import genai
from openai import OpenAI
from pydantic import BaseModel


T = TypeVar("T", bound=BaseModel)

# Default retry settings (can be overridden per call)
DEFAULT_MAX_RETRIES: int = 3
DEFAULT_RETRY_DELAY: int = 2  # seconds between retries

# OpenAI models that don't support the temperature parameter (reasoning models)
NO_TEMPERATURE_MODELS = {"gpt-5.2-pro", "gpt-5-mini", "gpt-5-nano", "o1", "o1-mini", "o1-preview"}


@dataclass
class LLMResult:
    """Full result of an LLM call.

    ``provider.call()`` returns this for every provider. ``content`` is the
    parsed Pydantic model (when ``response_model`` is given) or the full text
    (all content blocks joined). The remaining fields carry everything the
    old ``(content, input_tokens, output_tokens)`` tuple discarded.
    """

    content: Any
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: Optional[int] = None
    model: Optional[str] = None
    stop_reason: Optional[str] = None
    raw_text: Optional[str] = None       # provider text pre-parse (None when N/A)
    reasoning_text: Optional[str] = None  # reasoning/thinking content; None otherwise
    raw: Any = field(default=None, repr=False)  # untouched SDK response


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (openai, gemini, perplexity, anthropic, grok)."""
        pass

    @abstractmethod
    def call(
        self,
        messages: List[Dict[str, str]],
        model: str,
        *,
        response_model: Optional[Type[T]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        **provider_kwargs: Any,
    ) -> "LLMResult":
        """
        Call the LLM.

        Parameters
        ----------
        messages : list of {"role": str, "content": str}
            Conversation messages.
        model : str
            Model identifier.
        response_model : Type[BaseModel], optional
            If provided, return a parsed Pydantic instance. Otherwise return str.
        temperature : float, optional
            Sampling temperature. Omit to use provider default.
        max_tokens : int, optional
            Maximum tokens in response.
        max_retries : int
            Retry attempts on transient errors.
        retry_delay : float
            Seconds between retries.
        **provider_kwargs
            Forwarded directly to the underlying SDK call.

        Returns
        -------
        LLMResult
            Full result object; ``.content`` is the parsed model (when
            ``response_model`` is given) or the full text, plus token/cache/
            latency/reasoning metadata.
        """
        ...

    def call_structured(self, messages, response_model, model, temperature=0.7,
                       max_tokens=None, **kwargs):
        """Legacy alias. Returns an LLMResult (use call(response_model=...))."""
        return self.call(messages, model, response_model=response_model,
                        temperature=temperature, max_tokens=max_tokens, **kwargs)

    def call_text(self, messages, model, temperature=0.7, max_tokens=None, **kwargs):
        """Legacy alias. Returns an LLMResult (use call())."""
        return self.call(messages, model, temperature=temperature,
                        max_tokens=max_tokens, **kwargs)


class OpenAIProvider(LLMProvider):
    """OpenAI API provider."""

    def __init__(self, *, client: Any = None, api_key: Optional[str] = None):
        import os

        if client is not None:
            self._client = client
        else:
            resolved_key = api_key or os.environ.get("OPENAI_API_KEY")
            if not resolved_key:
                raise ValueError(
                    "OpenAI API key required. Pass api_key= or set OPENAI_API_KEY."
                )
            self._client = OpenAI(api_key=resolved_key)

    @property
    def name(self) -> str:
        return "openai"

    def call(
        self,
        messages: List[Dict[str, str]],
        model: str,
        *,
        response_model: Optional[Type[T]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        **provider_kwargs: Any,
    ) -> "LLMResult":
        """Call the OpenAI API."""
        for attempt in range(1, max_retries + 1):
            try:
                if response_model is not None:
                    return self._call_structured(
                        messages, model, response_model,
                        temperature=temperature,
                        **provider_kwargs,
                    )
                return self._call_text(
                    messages, model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **provider_kwargs,
                )
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(retry_delay)
                else:
                    raise RuntimeError(
                        f"OpenAI call failed after {max_retries} attempts"
                    ) from e

        raise RuntimeError(f"OpenAI call failed after {max_retries} attempts")

    def _call_text(
        self,
        messages: List[Dict[str, str]],
        model: str,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **provider_kwargs: Any,
    ) -> "LLMResult":
        args: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            **provider_kwargs,
        }
        if temperature is not None and model not in NO_TEMPERATURE_MODELS:
            args["temperature"] = temperature
        if max_tokens:
            args["max_tokens"] = max_tokens

        t0 = time.monotonic()
        response = self._client.chat.completions.create(**args)
        latency_ms = int((time.monotonic() - t0) * 1000)
        usage = response.usage
        details = getattr(usage, "prompt_tokens_details", None)
        cache_read = getattr(details, "cached_tokens", 0) or 0
        choice = response.choices[0]
        return LLMResult(
            content=choice.message.content,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            cache_read_tokens=cache_read,
            latency_ms=latency_ms,
            model=getattr(response, "model", model),
            stop_reason=getattr(choice, "finish_reason", None),
            raw_text=choice.message.content,
            raw=response,
        )

    def _call_structured(
        self,
        messages: List[Dict[str, str]],
        model: str,
        response_model: Type[T],
        *,
        temperature: Optional[float] = None,
        **provider_kwargs: Any,
    ) -> "LLMResult":
        parse_args: Dict[str, Any] = {
            "model": model,
            "input": messages,
            "text_format": response_model,
            **provider_kwargs,
        }
        if temperature is not None and model not in NO_TEMPERATURE_MODELS:
            parse_args["temperature"] = temperature

        t0 = time.monotonic()
        response = self._client.responses.parse(**parse_args)
        latency_ms = int((time.monotonic() - t0) * 1000)
        usage = response.usage
        details = getattr(usage, "input_tokens_details", None)
        cache_read = getattr(details, "cached_tokens", 0) or 0
        reasoning = getattr(response, "output_reasoning", None)
        reasoning_text = getattr(reasoning, "summary", None) if reasoning is not None else None
        return LLMResult(
            content=response.output_parsed,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=cache_read,
            latency_ms=latency_ms,
            model=getattr(response, "model", model),
            stop_reason=getattr(response, "status", None),
            raw_text=getattr(response, "output_text", None),
            reasoning_text=reasoning_text,
            raw=response,
        )



class GeminiProvider(LLMProvider):
    """Google Gemini API provider using google.genai."""

    def __init__(self, *, api_key: Optional[str] = None):
        """
        Initialize with Gemini API key.

        Parameters
        ----------
        api_key : str, optional
            Gemini API key. Falls back to GEMINI_API_KEY env var.
        """
        import os

        resolved_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not resolved_key:
            raise ValueError(
                "Gemini API key required. Pass api_key= or set GEMINI_API_KEY."
            )
        self._api_key = resolved_key
        # Temporarily unset GOOGLE_API_KEY to prevent auto-detection conflicts
        google_api_key_backup = os.environ.pop("GOOGLE_API_KEY", None)
        try:
            self._client = genai.Client(api_key=resolved_key)
        finally:
            if google_api_key_backup is not None:
                os.environ["GOOGLE_API_KEY"] = google_api_key_backup

    @property
    def name(self) -> str:
        return "gemini"

    def call(
        self,
        messages: List[Dict[str, str]],
        model: str,
        *,
        response_model: Optional[Type[T]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        **provider_kwargs: Any,
    ) -> "LLMResult":
        """Call the Gemini API."""
        import json
        from google.genai import types

        # Convert messages to Gemini format
        system_prompt = None
        contents = []
        for msg in messages:
            if msg["role"] == "system":
                system_prompt = msg["content"]
            elif msg["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": msg["content"]}]})
            elif msg["role"] == "assistant":
                contents.append({"role": "model", "parts": [{"text": msg["content"]}]})

        if response_model is not None:
            schema_str = json.dumps(response_model.model_json_schema(), indent=2)
            schema_instruction = (
                "\n\nIMPORTANT: Respond ONLY with valid JSON matching this schema:\n"
                + schema_str
            )
            system_prompt = (system_prompt or "") + schema_instruction

        config_kwargs: Dict[str, Any] = {
            "system_instruction": system_prompt,
        }
        if response_model is not None:
            config_kwargs["response_mime_type"] = "application/json"

        config = types.GenerateContentConfig(**config_kwargs)

        for attempt in range(1, max_retries + 1):
            try:
                t0 = time.monotonic()
                response = self._client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
                latency_ms = int((time.monotonic() - t0) * 1000)

                usage = response.usage_metadata
                in_tokens = usage.prompt_token_count or 0
                out_tokens = usage.candidates_token_count or 0
                cache_read = getattr(usage, "cached_content_token_count", 0) or 0
                raw_text = response.text

                content = (
                    response_model.model_validate(json.loads(raw_text))
                    if response_model is not None
                    else raw_text
                )
                return LLMResult(
                    content=content,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    cache_read_tokens=cache_read,
                    latency_ms=latency_ms,
                    model=getattr(response, "model_version", None) or model,
                    raw_text=raw_text,
                    raw=response,
                )

            except Exception as e:
                if attempt < max_retries:
                    time.sleep(retry_delay)
                else:
                    raise RuntimeError(
                        f"Gemini call failed after {max_retries} attempts"
                    ) from e

        raise RuntimeError(f"Gemini call failed after {max_retries} attempts")


class PerplexityProvider(LLMProvider):
    """Perplexity API provider (OpenAI-compatible)."""

    def __init__(self, *, api_key: Optional[str] = None, base_url: str = "https://api.perplexity.ai"):
        """
        Initialize Perplexity provider.

        Parameters
        ----------
        api_key : str, optional
            Perplexity API key. Falls back to PERPLEXITY_API_KEY env var.
        base_url : str
            Perplexity API base URL
        """
        import os

        resolved_key = api_key or os.environ.get("PERPLEXITY_API_KEY")
        if not resolved_key:
            raise ValueError(
                "Perplexity API key required. Pass api_key= or set PERPLEXITY_API_KEY."
            )
        self._client = OpenAI(api_key=resolved_key, base_url=base_url)

    @property
    def name(self) -> str:
        return "perplexity"

    def call(
        self,
        messages: List[Dict[str, str]],
        model: str,
        *,
        response_model: Optional[Type[T]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        **provider_kwargs: Any,
    ) -> "LLMResult":
        """Call the Perplexity API."""
        import json

        # Filter out unsupported kwargs (Perplexity has built-in web search)
        provider_kwargs.pop("tools", None)

        # If structured output requested, inject JSON schema into system message
        if response_model is not None:
            schema_str = response_model.model_json_schema()
            schema_instruction = (
                f"IMPORTANT: Respond ONLY with valid JSON matching this schema:\n"
                f"{json.dumps(schema_str, indent=2)}"
            )
            modified_messages = []
            has_system = any(msg["role"] == "system" for msg in messages)
            if not has_system:
                modified_messages.append({"role": "system", "content": schema_instruction})
            for msg in messages:
                if msg["role"] == "system":
                    modified_messages.append({
                        "role": "system",
                        "content": f"{msg['content']}\n\n{schema_instruction}",
                    })
                else:
                    modified_messages.append(msg)
        else:
            modified_messages = messages

        for attempt in range(1, max_retries + 1):
            try:
                args: Dict[str, Any] = {
                    "model": model,
                    "messages": modified_messages,
                    **provider_kwargs,
                }
                if temperature is not None:
                    args["temperature"] = temperature
                if max_tokens:
                    args["max_tokens"] = max_tokens

                t0 = time.monotonic()
                response = self._client.chat.completions.create(**args)
                latency_ms = int((time.monotonic() - t0) * 1000)

                usage = response.usage
                in_tokens = usage.prompt_tokens or 0
                out_tokens = usage.completion_tokens or 0
                choice = response.choices[0]
                msg_content = choice.message.content

                if response_model is not None:
                    text = msg_content
                    # Extract JSON from response (might be wrapped in markdown)
                    if "```json" in text:
                        text = text.split("```json")[1].split("```")[0]
                    elif "```" in text:
                        text = text.split("```")[1].split("```")[0]
                    content = response_model.model_validate(json.loads(text.strip()))
                else:
                    content = msg_content

                return LLMResult(
                    content=content,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    latency_ms=latency_ms,
                    model=getattr(response, "model", model),
                    stop_reason=getattr(choice, "finish_reason", None),
                    raw_text=msg_content,
                    raw=response,
                )

            except Exception as e:
                if attempt < max_retries:
                    time.sleep(retry_delay)
                else:
                    raise RuntimeError(
                        f"Perplexity call failed after {max_retries} attempts"
                    ) from e

        raise RuntimeError(f"Perplexity call failed after {max_retries} attempts")


class GrokProvider(LLMProvider):
    """xAI Grok API provider (OpenAI-compatible)."""

    def __init__(self, *, api_key=None, base_url="https://api.x.ai/v1"):
        import os
        key = api_key or os.environ.get("XAI_API_KEY")
        if not key:
            raise ValueError(
                "xAI API key required. Pass api_key= or set XAI_API_KEY."
            )
        self._client = OpenAI(api_key=key, base_url=base_url)

    @property
    def name(self) -> str:
        return "grok"

    def call(self, messages, model="grok-3", *, response_model=None,
             max_retries=DEFAULT_MAX_RETRIES, retry_delay=DEFAULT_RETRY_DELAY, **kwargs):
        import json
        if response_model is not None:
            messages = self._inject_json_schema(messages, response_model)
        for attempt in range(1, max_retries + 1):
            try:
                t0 = time.monotonic()
                response = self._client.chat.completions.create(model=model, messages=messages, **kwargs)
                latency_ms = int((time.monotonic() - t0) * 1000)
                usage = response.usage
                in_tokens = usage.prompt_tokens or 0
                out_tokens = usage.completion_tokens or 0
                choice = response.choices[0]
                text = choice.message.content
                content = (
                    response_model.model_validate(json.loads(text.strip()))
                    if response_model is not None
                    else text
                )
                return LLMResult(
                    content=content,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    latency_ms=latency_ms,
                    model=getattr(response, "model", model),
                    stop_reason=getattr(choice, "finish_reason", None),
                    raw_text=text,
                    raw=response,
                )
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(retry_delay)
                else:
                    raise RuntimeError(
                        f"Grok call failed after {max_retries} attempts"
                    ) from e
        raise RuntimeError(f"Grok call failed after {max_retries} attempts")

    @staticmethod
    def _inject_json_schema(messages, response_model):
        import json
        schema_str = json.dumps(response_model.model_json_schema(), indent=2)
        schema_instruction = (
            f"IMPORTANT: Respond ONLY with valid JSON matching this schema:\n"
            f"{schema_str}"
        )
        modified = []
        has_system = any(msg["role"] == "system" for msg in messages)
        if not has_system:
            modified.append({"role": "system", "content": schema_instruction})
        for msg in messages:
            if msg["role"] == "system":
                modified.append({
                    "role": "system",
                    "content": f"{msg['content']}\n\n{schema_instruction}",
                })
            else:
                modified.append(msg)
        return modified


class AnthropicProvider(LLMProvider):

    def __init__(self, *, api_key=None):
        import os

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError(
                "Anthropic API key required. Pass api_key= or set ANTHROPIC_API_KEY."
            )
        self._client = Anthropic(api_key=key)

    @property
    def name(self) -> str:
        return "anthropic"

    def call(self, messages, model="claude-sonnet-4-20250514", *, response_model=None,
             max_retries=DEFAULT_MAX_RETRIES, retry_delay=DEFAULT_RETRY_DELAY, **kwargs):
        system_prompt, filtered = self._extract_system(messages)
        args = {"model": model, "messages": filtered, "max_tokens": 4096, **kwargs}
        if system_prompt:
            args["system"] = system_prompt

        for attempt in range(1, max_retries + 1):
            try:
                if response_model is not None:
                    schema = response_model.model_json_schema()
                    tool_name = response_model.__name__
                    args["tools"] = [{
                        "name": tool_name,
                        "description": f"Provide {tool_name} data",
                        "input_schema": schema,
                    }]
                    args["tool_choice"] = {"type": "tool", "name": tool_name}

                t0 = time.monotonic()
                response = self._client.messages.create(**args)
                latency_ms = int((time.monotonic() - t0) * 1000)
                usage = response.usage

                if response_model is not None:
                    # Defect 2 fix: locate the tool_use block by type, not index 0
                    tool_block = next(
                        b for b in response.content
                        if getattr(b, "type", None) == "tool_use"
                    )
                    content = response_model.model_validate(tool_block.input)
                    raw_text = None
                else:
                    # Defect 1 fix: join ALL text blocks (was content[0].text)
                    raw_text = "".join(
                        b.text for b in response.content
                        if getattr(b, "type", None) == "text"
                    )
                    content = raw_text

                return LLMResult(
                    content=content,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                    latency_ms=latency_ms,
                    model=getattr(response, "model", model),
                    stop_reason=getattr(response, "stop_reason", None),
                    raw_text=raw_text,
                    raw=response,
                )
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(retry_delay)
                else:
                    raise RuntimeError(
                        f"Anthropic call failed after {max_retries} attempts"
                    ) from e

    @staticmethod
    def _extract_system(messages):
        system_prompt = None
        filtered = []
        for msg in messages:
            if msg["role"] == "system":
                system_prompt = msg["content"]
            else:
                filtered.append(msg)
        return system_prompt, filtered


# Model name mappings for each provider
OPENAI_MODELS = {
    # GPT-4 family
    "gpt-4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
    "gpt-4.5-preview": "gpt-4.5-preview",
    "gpt-4-turbo": "gpt-4-turbo",
    "gpt-3.5-turbo": "gpt-3.5-turbo",
    # GPT-5 family
    "gpt-5": "gpt-5",
    "gpt-5-mini": "gpt-5-mini",
    "gpt-5-nano": "gpt-5-nano",
    "gpt-5.1": "gpt-5.1",
    "gpt-5.2": "gpt-5.2",
    "gpt-5.2-pro": "gpt-5.2-pro",
}

GEMINI_MODELS = {
    # Gemini 2.x family
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.5-pro": "gemini-2.5-pro",
    # Gemini 3.x family
    "gemini-3-flash": "gemini-3-flash",
    "gemini-3-pro": "gemini-3-pro-preview",
    "gemini-3-deep-think": "gemini-3-pro-preview",  # Uses thinking mode
}

PERPLEXITY_MODELS = {
    "sonar": "sonar",
    "sonar-pro": "sonar-pro",
    "sonar-reasoning-pro": "sonar-reasoning-pro",
}

ANTHROPIC_MODELS = {
    "claude-opus-4-20250514": "claude-opus-4-20250514",
    "claude-sonnet-4-20250514": "claude-sonnet-4-20250514",
    "claude-haiku-3-5-20241022": "claude-haiku-3-5-20241022",
}

GROK_MODELS = {
    "grok-3": "grok-3",
    "grok-3-mini": "grok-3-mini",
    "grok-3-fast": "grok-3-fast",
}


def get_provider(
    provider_name: str,
    openai_client: Any = None,
    gemini_api_key: str = None,
    perplexity_api_key: str = None,
    *,
    api_key: Optional[str] = None,
) -> LLMProvider:
    """
    Get an LLM provider instance.

    Parameters
    ----------
    provider_name : str
        Provider name: "openai", "gemini", "perplexity", "anthropic", or "grok"
    openai_client : OpenAI, optional
        Pre-built OpenAI client (only honored if provider_name is "openai";
        otherwise pass ``api_key=``)
    gemini_api_key : str, optional
        Gemini API key (legacy; prefer ``api_key=``)
    perplexity_api_key : str, optional
        Perplexity API key (legacy; prefer ``api_key=``)
    api_key : str, optional
        API key for any provider. Falls back to each provider's conventional
        environment variable (OPENAI_API_KEY, GEMINI_API_KEY,
        PERPLEXITY_API_KEY, ANTHROPIC_API_KEY, XAI_API_KEY) when omitted.

    Returns
    -------
    LLMProvider
        Provider instance
    """
    if provider_name == "openai":
        if openai_client is not None:
            return OpenAIProvider(client=openai_client)
        return OpenAIProvider(api_key=api_key)

    elif provider_name == "gemini":
        return GeminiProvider(api_key=api_key or gemini_api_key)

    elif provider_name == "perplexity":
        return PerplexityProvider(api_key=api_key or perplexity_api_key)

    elif provider_name == "anthropic":
        return AnthropicProvider(api_key=api_key)

    elif provider_name == "grok":
        return GrokProvider(api_key=api_key)

    else:
        raise ValueError(f"Unknown provider: {provider_name}")
