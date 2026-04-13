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

    def __init__(self, client: Any):
        """
        Initialize with OpenAI client.

        Parameters
        ----------
        client : OpenAI
            OpenAI client instance
        """
        self._client = client

    @property
    def name(self) -> str:
        return "openai"

    def call_structured(
        self,
        messages: List[Dict[str, str]],
        response_model: Type[T],
        model: str = "gpt-4o",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: int = DEFAULT_RETRY_DELAY,
        **kwargs: Any,
    ) -> Tuple[T, int, int]:
        """Call OpenAI with structured output using responses.parse."""
        from openai import RateLimitError

        attempt = 1
        total_input_tokens = 0
        total_output_tokens = 0

        while attempt <= max_retries:
            try:
                # Build args, conditionally including temperature
                parse_args = {
                    "model": model,
                    "input": messages,
                    "text_format": response_model,
                    **kwargs,
                }
                if model not in NO_TEMPERATURE_MODELS:
                    parse_args["temperature"] = temperature

                response = self._client.responses.parse(**parse_args)
                total_input_tokens += response.usage.input_tokens
                total_output_tokens += response.usage.output_tokens
                return (
                    response.output_parsed,
                    total_input_tokens,
                    total_output_tokens,
                )

            except RateLimitError as e:
                msg = str(e).lower()
                if "insufficient" in msg or "quota" in msg or "billing" in msg:
                    raise RuntimeError("OpenAI quota exceeded") from e
                print(f"Rate limit hit, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                attempt += 1

            except Exception as e:
                print(f"Error on attempt {attempt}/{max_retries}: {e}")
                attempt += 1
                time.sleep(retry_delay)

        raise RuntimeError(f"OpenAI call failed after {max_retries} attempts")

    def call_text(
        self,
        messages: List[Dict[str, str]],
        model: str = "gpt-4o",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: int = DEFAULT_RETRY_DELAY,
        **kwargs: Any,
    ) -> Tuple[str, int, int]:
        """Call OpenAI for plain text output."""
        from openai import RateLimitError

        attempt = 1
        total_input_tokens = 0
        total_output_tokens = 0

        while attempt <= max_retries:
            try:
                # Build args, conditionally including temperature
                args = {
                    "model": model,
                    "messages": messages,
                    **kwargs,
                }
                if model not in NO_TEMPERATURE_MODELS:
                    args["temperature"] = temperature
                if max_tokens:
                    args["max_tokens"] = max_tokens

                response = self._client.chat.completions.create(**args)
                total_input_tokens += response.usage.prompt_tokens
                total_output_tokens += response.usage.completion_tokens
                return (
                    response.choices[0].message.content,
                    total_input_tokens,
                    total_output_tokens,
                )

            except RateLimitError as e:
                msg = str(e).lower()
                if "insufficient" in msg or "quota" in msg or "billing" in msg:
                    raise RuntimeError("OpenAI quota exceeded") from e
                print(f"Rate limit hit, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                attempt += 1

            except Exception as e:
                print(f"Error on attempt {attempt}/{max_retries}: {e}")
                attempt += 1
                time.sleep(retry_delay)

        raise RuntimeError(f"OpenAI call failed after {max_retries} attempts")


class GeminiProvider(LLMProvider):
    """Google Gemini API provider using google.genai."""

    def __init__(self, api_key: str):
        """
        Initialize with Gemini API key.

        Parameters
        ----------
        api_key : str
            Gemini API key
        """
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "gemini"

    def call_structured(
        self,
        messages: List[Dict[str, str]],
        response_model: Type[T],
        model: str = "gemini-2.0-flash",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: int = DEFAULT_RETRY_DELAY,
        **kwargs: Any,
    ) -> Tuple[T, int, int]:
        """
        Call Gemini with structured output.

        Uses JSON-in-prompt approach for reliability across all Pydantic models.
        """
        import json
        import os
        from google import genai
        from google.genai import types

        # Temporarily unset GOOGLE_API_KEY to prevent auto-detection
        google_api_key_backup = os.environ.pop("GOOGLE_API_KEY", None)
        try:
            client = genai.Client(api_key=self._api_key)
        finally:
            if google_api_key_backup is not None:
                os.environ["GOOGLE_API_KEY"] = google_api_key_backup

        # Build schema string for prompt
        schema_str = json.dumps(response_model.model_json_schema(), indent=2)

        # Convert messages to Gemini format, adding JSON instruction to system prompt
        system_prompt = None
        contents = []
        for msg in messages:
            if msg["role"] == "system":
                system_prompt = (
                    f"{msg['content']}\n\n"
                    f"IMPORTANT: Respond ONLY with valid JSON matching this schema:\n"
                    f"{schema_str}"
                )
            elif msg["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": msg["content"]}]})
            elif msg["role"] == "assistant":
                contents.append({"role": "model", "parts": [{"text": msg["content"]}]})

        attempt = 1
        total_input_tokens = 0
        total_output_tokens = 0

        while attempt <= max_retries:
            try:
                config = types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    response_mime_type="application/json",
                    system_instruction=system_prompt,
                )

                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )

                # Track tokens even if parsing fails
                if hasattr(response, 'usage_metadata') and response.usage_metadata:
                    total_input_tokens += response.usage_metadata.prompt_token_count or 0
                    total_output_tokens += response.usage_metadata.candidates_token_count or 0

                # Parse JSON response into Pydantic model
                parsed = response_model.model_validate(json.loads(response.text))

                return parsed, total_input_tokens, total_output_tokens

            except Exception as e:
                print(f"Error on attempt {attempt}/{max_retries}: {e}")
                attempt += 1
                time.sleep(retry_delay)

        raise RuntimeError(f"Gemini call failed after {max_retries} attempts")

    def call_text(
        self,
        messages: List[Dict[str, str]],
        model: str = "gemini-2.0-flash",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: int = DEFAULT_RETRY_DELAY,
        **kwargs: Any,
    ) -> Tuple[str, int, int]:
        """Call Gemini for plain text output."""
        import os
        from google import genai
        from google.genai import types

        # Temporarily unset GOOGLE_API_KEY to prevent auto-detection
        google_api_key_backup = os.environ.pop("GOOGLE_API_KEY", None)
        try:
            client = genai.Client(api_key=self._api_key)
        finally:
            if google_api_key_backup is not None:
                os.environ["GOOGLE_API_KEY"] = google_api_key_backup

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

        attempt = 1
        total_input_tokens = 0
        total_output_tokens = 0

        while attempt <= max_retries:
            try:
                config = types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    system_instruction=system_prompt,
                )

                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )

                # Track tokens
                if hasattr(response, 'usage_metadata') and response.usage_metadata:
                    total_input_tokens += response.usage_metadata.prompt_token_count or 0
                    total_output_tokens += response.usage_metadata.candidates_token_count or 0

                return response.text, total_input_tokens, total_output_tokens

            except Exception as e:
                print(f"Error on attempt {attempt}/{max_retries}: {e}")
                attempt += 1
                time.sleep(retry_delay)

        raise RuntimeError(f"Gemini call failed after {max_retries} attempts")


class PerplexityProvider(LLMProvider):
    """Perplexity API provider (OpenAI-compatible)."""

    def __init__(self, api_key: str, base_url: str = "https://api.perplexity.ai"):
        """
        Initialize Perplexity provider.

        Parameters
        ----------
        api_key : str
            Perplexity API key
        base_url : str
            Perplexity API base URL
        """
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)

    @property
    def name(self) -> str:
        return "perplexity"

    def call_structured(
        self,
        messages: List[Dict[str, str]],
        response_model: Type[T],
        model: str = "sonar",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: int = DEFAULT_RETRY_DELAY,
        **kwargs: Any,
    ) -> Tuple[T, int, int]:
        """
        Call Perplexity with structured output.

        Note: Perplexity doesn't natively support structured output,
        so we request JSON and parse it manually.
        """
        import json

        # Add JSON instruction to system message
        modified_messages = []
        for msg in messages:
            if msg["role"] == "system":
                schema_str = response_model.model_json_schema()
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

        attempt = 1
        total_input_tokens = 0
        total_output_tokens = 0

        while attempt <= max_retries:
            try:
                # Filter out unsupported kwargs (Perplexity has built-in web search)
                filtered_kwargs = {k: v for k, v in kwargs.items() if k != "tools"}

                args = {
                    "model": model,
                    "messages": modified_messages,
                    "temperature": temperature,
                    **filtered_kwargs,
                }
                if max_tokens:
                    args["max_tokens"] = max_tokens

                response = self._client.chat.completions.create(**args)

                # Track tokens before parsing (in case parsing fails)
                if hasattr(response, 'usage') and response.usage:
                    total_input_tokens += response.usage.prompt_tokens or 0
                    total_output_tokens += response.usage.completion_tokens or 0

                text = response.choices[0].message.content

                # Extract JSON from response (might be wrapped in markdown)
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0]
                elif "```" in text:
                    text = text.split("```")[1].split("```")[0]

                parsed = response_model.model_validate(json.loads(text.strip()))

                return (
                    parsed,
                    total_input_tokens,
                    total_output_tokens,
                )

            except Exception as e:
                print(f"Error on attempt {attempt}/{max_retries}: {e}")
                attempt += 1
                time.sleep(retry_delay)

        raise RuntimeError(f"Perplexity call failed after {max_retries} attempts")

    def call_text(
        self,
        messages: List[Dict[str, str]],
        model: str = "sonar",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay: int = DEFAULT_RETRY_DELAY,
        **kwargs: Any,
    ) -> Tuple[str, int, int]:
        """Call Perplexity for plain text output (with web search)."""
        attempt = 1
        total_input_tokens = 0
        total_output_tokens = 0

        while attempt <= max_retries:
            try:
                # Filter out unsupported kwargs (Perplexity has built-in web search)
                filtered_kwargs = {k: v for k, v in kwargs.items() if k != "tools"}

                args = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    **filtered_kwargs,
                }
                if max_tokens:
                    args["max_tokens"] = max_tokens

                response = self._client.chat.completions.create(**args)

                # Track tokens
                if hasattr(response, 'usage') and response.usage:
                    total_input_tokens += response.usage.prompt_tokens or 0
                    total_output_tokens += response.usage.completion_tokens or 0

                return (
                    response.choices[0].message.content,
                    total_input_tokens,
                    total_output_tokens,
                )

            except Exception as e:
                print(f"Error on attempt {attempt}/{max_retries}: {e}")
                attempt += 1
                time.sleep(retry_delay)

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
        return OpenAIProvider(openai_client)

    elif provider_name == "gemini":
        if gemini_api_key is None:
            raise ValueError("gemini_api_key required for Gemini provider")
        return GeminiProvider(gemini_api_key)

    elif provider_name == "perplexity":
        if perplexity_api_key is None:
            raise ValueError("perplexity_api_key required for Perplexity provider")
        return PerplexityProvider(perplexity_api_key)

    else:
        raise ValueError(f"Unknown provider: {provider_name}")
