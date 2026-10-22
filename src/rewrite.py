"""Provider-independent rewrite orchestration and output validation.

This module keeps provider-specific construction at the edge. The endpoint
works with a small async client boundary, while this module owns prompt
assembly, structured output validation and retries.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re

import time
from typing import Any

from pydantic import BaseModel

from .config import ServiceConfig


# ---------------------------------------------------------------------------
# Errors, result model and validation patterns
# ---------------------------------------------------------------------------


class RewriteError(Exception):
    """Base error for model invocation and output validation."""


class RewriteProviderError(RewriteError):
    """The provider could not produce a response."""


class RewriteValidationError(RewriteError):
    """The provider returned unusable or unsafe output."""


class RewriteResult(BaseModel):
    """Validated text returned by a rewrite provider."""

    generated_text: str


_REFUSAL_PATTERN = re.compile(
    r"^(?:i(?:'|’)m sorry|sorry|ik kan deze tekst niet|ik kan niet helpen)", re.IGNORECASE
)
_PREAMBLE_PATTERN = re.compile(
    r"^(?:hier is de herschreven tekst|hieronder staat de herschreven tekst)\s*:\s*",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Language-model client and provider construction
# ---------------------------------------------------------------------------


class LangChainLlmClient:
    """Small async client boundary around a configured LangChain runnable."""

    def __init__(self, model: Any) -> None:
        """Store the configured runnable without coupling callers to LangChain."""

        self.model = model

    async def ainvoke(self, messages: list[dict[str, str]]) -> Any:
        """Invoke either an async or synchronous LangChain runnable."""

        result = self.model.ainvoke(messages)
        if inspect.isawaitable(result):
            return await result
        return result


def make_llm(config: ServiceConfig) -> Any:
    """Build the configured provider client with structured output enabled."""

    provider = config.llm.provider.lower()
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=config.llm.model_name,
            api_key=config.llm.api_key,
            base_url=config.llm.base_url,
            temperature=config.llm.temperature,
            timeout=config.llm.timeout,
        )
        return LangChainLlmClient(model.with_structured_output(RewriteResult, include_raw=True))
    
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        model = ChatOllama(
            model=config.llm.model_name,
            base_url=config.llm.base_url,
            temperature=config.llm.temperature,
        )
        return LangChainLlmClient(model.with_structured_output(RewriteResult, include_raw=True))
    
    if provider == "mistralai":
        from langchain_mistralai import ChatMistralAI

        model = ChatMistralAI(
            model=config.llm.model_name,
            api_key=config.llm.api_key,
            endpoint=config.llm.base_url,
            temperature=config.llm.temperature,
        )
        return LangChainLlmClient(model.with_structured_output(RewriteResult, include_raw=True))
    raise ValueError(f"Unsupported LLM provider: {config.llm.provider}")


# ---------------------------------------------------------------------------
# Prompt and provider-output handling
# ---------------------------------------------------------------------------


def assemble_prompt(
    config: ServiceConfig,
    *,
    field: str,
    address_form: str,
    guideline_text: str,
    text: str,
) -> list[dict[str, str]]:
    """Render the configured system and user messages for one rewrite."""

    values = {
        "field": field,
        "address_form": address_form,
        "guidelines": guideline_text,
        "text": text,
    }
    return [
        {"role": "system", "content": config.rewrite.prompt.system_message.format(**values)},
        {"role": "user", "content": config.rewrite.prompt.user_message.format(**values)},
    ]


def validate_output(value: Any) -> RewriteResult:
    """Normalize provider output and reject empty, prefaced or refused text."""

    if isinstance(value, RewriteResult):
        text = value.generated_text
    elif isinstance(value, dict):
        text = value.get("generated_text") or value.get("generated-text") or value.get("text")
    elif hasattr(value, "generated_text"):
        text = value.generated_text
    elif hasattr(value, "content"):
        text = value.content
    else:
        text = value
    if not isinstance(text, str):
        raise RewriteValidationError("Model output did not contain text")
    text = text.strip()
    if _PREAMBLE_PATTERN.match(text):
        raise RewriteValidationError("Model output contained a preamble")
    if not text:
        raise RewriteValidationError("Model output was empty")
    if _REFUSAL_PATTERN.match(text):
        raise RewriteValidationError("Model refused the rewrite")
    return RewriteResult(generated_text=text)


def _decode_response(response: Any) -> Any:
    """Decode JSON returned as a string while preserving ordinary text output."""

    if not isinstance(response, str):
        return response
    try:
        decoded = json.loads(response)
    except json.JSONDecodeError:
        return response
    return decoded


# ---------------------------------------------------------------------------
# Invocation workflow
# ---------------------------------------------------------------------------


async def _invoke(llm: Any, messages: list[dict[str, str]]) -> Any:
    """Invoke an injected client using its async or synchronous interface."""

    method = getattr(llm, "ainvoke", None)
    if method is not None:
        return await method(messages)
    result = llm.invoke(messages)
    if inspect.isawaitable(result):
        return await result
    return result


def log_response(response: Any, processing_time: float) -> None:
    """Print parsed output, token usage, metadata, and LLM processing time."""

    print(f"LLM processing time: {processing_time:.3f}s")

    if isinstance(response, dict):
        raw = response.get("raw")

        if raw is not None:
            usage = getattr(raw, "usage_metadata", None)
            if usage is not None:
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
                total_tokens = usage.get("total_tokens")

                print(
                    f"Input: {input_tokens}, "
                    f"Output: {output_tokens}, "
                    f"Total: {total_tokens}"
                )


async def rewrite_text(
    config: ServiceConfig,
    llm: Any,
    *,
    field: str,
    address_form: str,
    guideline_text: str,
    text: str,
) -> RewriteResult:
    """Run one rewrite with retry handling."""

    messages = assemble_prompt(
        config,
        field=field,
        address_form=address_form,
        guideline_text=guideline_text,
        text=text,
    )

    # Retry transient transport failures, but never retry invalid model output.
    start_time = time.perf_counter()

    for attempt in range(config.limits.retries + 1):

        try:
            response = await asyncio.wait_for(
                _invoke(llm, messages),
                timeout=config.llm.timeout,
            )

            processing_time = time.perf_counter() - start_time

            # Log the response for debugging purposes before validation.
            log_response(response, processing_time)

            if isinstance(response, dict) and "parsed" in response:
                if response["parsing_error"]:
                    raise RewriteValidationError(
                        "Model output parsing failed"
                    )

                return validate_output(response["parsed"])

            return validate_output(_decode_response(response))

        except RewriteValidationError:
            raise

        except (ConnectionError, TimeoutError, asyncio.TimeoutError) as error:
            if attempt >= config.limits.retries:
                raise RewriteProviderError(
                    "Model provider did not respond"
                ) from error

        except Exception as error:
            raise RewriteProviderError("Model provider failed") from error

    raise RewriteProviderError("Model provider failed")