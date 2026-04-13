"""
Multi-model LLM provider abstraction.

Supports:
- OpenAI (gpt-4o, gpt-4o-mini, gpt-4.5-preview, gpt-5 family)
- Google Gemini (gemini-2.0-flash, gemini-1.5-pro, gemini-3 family)
- Perplexity (sonar, sonar-pro, sonar-reasoning-pro)
"""

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, Type, TypeVar

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
    ) -> Tuple[Any, int, int]:
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
        Tuple[T | str, int, int]
            (parsed_model_or_text, input_tokens, output_tokens)
        """
        ...


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
    ) -> Tuple[Any, int, int]:
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
    ) -> Tuple[str, int, int]:
        args: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            **provider_kwargs,
        }
        if temperature is not None and model not in NO_TEMPERATURE_MODELS:
            args["temperature"] = temperature
        if max_tokens:
            args["max_tokens"] = max_tokens

        response = self._client.chat.completions.create(**args)
        return (
            response.choices[0].message.content,
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
        )

    def _call_structured(
        self,
        messages: List[Dict[str, str]],
        model: str,
        response_model: Type[T],
        *,
        temperature: Optional[float] = None,
        **provider_kwargs: Any,
    ) -> Tuple[T, int, int]:
        parse_args: Dict[str, Any] = {
            "model": model,
            "input": messages,
            "text_format": response_model,
            **provider_kwargs,
        }
        if temperature is not None and model not in NO_TEMPERATURE_MODELS:
            parse_args["temperature"] = temperature

        response = self._client.responses.parse(**parse_args)
        return (
            response.output_parsed,
            response.usage.input_tokens,
            response.usage.output_tokens,
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
    ) -> Tuple[Any, int, int]:
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

        config_kwargs: Dict[str, Any] = {
            "system_instruction": system_prompt,
        }
        if response_model is not None:
            config_kwargs["response_mime_type"] = "application/json"

        config = types.GenerateContentConfig(**config_kwargs)

        for attempt in range(1, max_retries + 1):
            try:
                response = self._client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )

                in_tokens = response.usage_metadata.prompt_token_count or 0
                out_tokens = response.usage_metadata.candidates_token_count or 0

                if response_model is not None:
                    parsed = response_model.model_validate(json.loads(response.text))
                    return parsed, in_tokens, out_tokens

                return response.text, in_tokens, out_tokens

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
    ) -> Tuple[Any, int, int]:
        """Call the Perplexity API."""
        import json

        # Filter out unsupported kwargs (Perplexity has built-in web search)
        provider_kwargs.pop("tools", None)

        # If structured output requested, inject JSON schema into system message
        if response_model is not None:
            schema_str = response_model.model_json_schema()
            modified_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    modified_messages.append({
                        "role": "system",
                        "content": (
                            f"{msg['content']}\n\n"
                            f"IMPORTANT: Respond ONLY with valid JSON matching this schema:\n"
                            f"{json.dumps(schema_str, indent=2)}"
                        ),
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

                response = self._client.chat.completions.create(**args)

                in_tokens = response.usage.prompt_tokens or 0
                out_tokens = response.usage.completion_tokens or 0

                if response_model is not None:
                    text = response.choices[0].message.content
                    # Extract JSON from response (might be wrapped in markdown)
                    if "```json" in text:
                        text = text.split("```json")[1].split("```")[0]
                    elif "```" in text:
                        text = text.split("```")[1].split("```")[0]
                    parsed = response_model.model_validate(json.loads(text.strip()))
                    return parsed, in_tokens, out_tokens

                return (
                    response.choices[0].message.content,
                    in_tokens,
                    out_tokens,
                )

            except Exception as e:
                if attempt < max_retries:
                    time.sleep(retry_delay)
                else:
                    raise RuntimeError(
                        f"Perplexity call failed after {max_retries} attempts"
                    ) from e

        raise RuntimeError(f"Perplexity call failed after {max_retries} attempts")


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
    # Gemini 1.x family
    "gemini-1.5-pro": "gemini-1.5-pro",
    "gemini-1.5-flash": "gemini-1.5-flash",
    # Gemini 2.x family
    "gemini-2.0-flash": "gemini-2.0-flash-exp",
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


def get_provider(
    provider_name: str,
    openai_client: Any = None,
    gemini_api_key: str = None,
    perplexity_api_key: str = None,
) -> LLMProvider:
    """
    Get an LLM provider instance.

    Parameters
    ----------
    provider_name : str
        Provider name: "openai", "gemini", or "perplexity"
    openai_client : OpenAI, optional
        OpenAI client (required if provider_name is "openai")
    gemini_api_key : str, optional
        Gemini API key (required if provider_name is "gemini")
    perplexity_api_key : str, optional
        Perplexity API key (required if provider_name is "perplexity")

    Returns
    -------
    LLMProvider
        Provider instance
    """
    if provider_name == "openai":
        if openai_client is None:
            raise ValueError("openai_client required for OpenAI provider")
        return OpenAIProvider(client=openai_client)

    elif provider_name == "gemini":
        if gemini_api_key is None:
            raise ValueError("gemini_api_key required for Gemini provider")
        return GeminiProvider(api_key=gemini_api_key)

    elif provider_name == "perplexity":
        if perplexity_api_key is None:
            raise ValueError("perplexity_api_key required for Perplexity provider")
        return PerplexityProvider(api_key=perplexity_api_key)

    else:
        raise ValueError(f"Unknown provider: {provider_name}")
