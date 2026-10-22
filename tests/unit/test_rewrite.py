"""Tests for provider adaptation, prompt assembly and rewrite validation."""

import asyncio

import pytest

from src.config import ServiceConfig
from src.rewrite import (
    LangChainLlmClient,
    RewriteValidationError,
    assemble_prompt,
    rewrite_text,
    validate_output,
)


# ---------------------------------------------------------------------------
# Client boundary
# ---------------------------------------------------------------------------


def test_langchain_llm_client_delegates_async_invocation() -> None:
    """Delegate async invocation through the provider-neutral client."""

    class Model:
        async def ainvoke(self, messages):
            return {"generated_text": "Vul het formulier in."}

    result = asyncio.run(LangChainLlmClient(Model()).ainvoke([]))

    assert result == {"generated_text": "Vul het formulier in."}


# ---------------------------------------------------------------------------
# Prompt and output validation
# ---------------------------------------------------------------------------


def test_prompt_contains_field_guidelines_address_form_and_text() -> None:
    """Include all rewrite context in the assembled prompt."""

    config = ServiceConfig()

    messages = assemble_prompt(
        config,
        field="dct:title",
        address_form="nl-be-x-formal",
        guideline_text="Gebruik actieve zinnen.",
        text="De aanvrager vult het formulier in.",
    )

    prompt = "\n".join(message["content"] for message in messages)
    assert "dct:title" in prompt
    assert "nl-be-x-formal" in prompt
    assert "Gebruik actieve zinnen." in prompt
    assert "De aanvrager vult het formulier in." in prompt


def test_validate_output_rejects_empty_refusal_and_preamble() -> None:
    """Reject output that cannot be safely returned as a rewrite."""

    with pytest.raises(RewriteValidationError):
        validate_output(" ")
    with pytest.raises(RewriteValidationError):
        validate_output("Sorry, ik kan deze tekst niet herschrijven.")
    with pytest.raises(RewriteValidationError):
        validate_output("Hier is de herschreven tekst: Vul het formulier in.")


# ---------------------------------------------------------------------------
# Retry workflow
# ---------------------------------------------------------------------------


def test_rewrite_text_retries_connection_failure() -> None:
    """Retry a transient connection failure and return the later result."""

    class FlakyLLM:
        calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("temporary")
            return {"generated_text": "Vul het formulier in."}

    config = ServiceConfig()
    result = asyncio.run(
        rewrite_text(
            config,
            FlakyLLM(),
            field="dct:title",
            address_form="nl-be-x-formal",
            guideline_text="Gebruik actieve zinnen.",
            text="De aanvrager vult het formulier in.",
        )
    )

    assert result.generated_text == "Vul het formulier in."