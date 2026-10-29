"""Tests for provider adaptation, prompt assembly and rewrite validation."""

import asyncio

import pytest

from src.config import FieldLabel, ServiceConfig
from src.rewrite import (
    LangChainLlmClient,
    RewriteValidationError,
    address_form_label,
    assemble_prompt,
    extract_usage,
    field_label,
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


def test_field_label_uses_subject_type_to_disambiguate_predicates() -> None:
    """Map a shared predicate to the field of the subject that owns it."""

    config = ServiceConfig()
    description = "http://purl.org/dc/terms/description"

    assert field_label(config, ["http://data.europa.eu/m8g/Requirement"], description) == "Voorwaarden"
    assert field_label(config, ["http://purl.org/vocab/cpsv#Rule"], description) == "Procedure"
    assert field_label(config, ["http://example.org/Unknown"], description) == description


def test_field_label_falls_back_to_type_independent_entry() -> None:
    """Use an entry without subject type when no scoped entry matches."""

    config = ServiceConfig()
    config.rewrite.field_labels.append(FieldLabel(field="http://example.org/note", label="Notitie"))

    assert field_label(config, [], "http://example.org/note") == "Notitie"


def test_address_form_label_maps_language_variants() -> None:
    """Translate instance language variants to the guideline address form."""

    config = ServiceConfig()

    assert address_form_label(config, "nl-be-x-formal") == "u-vorm"
    assert address_form_label(config, "nl-be-x-informal") == "je-vorm"
    assert address_form_label(config, "onbekend") == "onbekend"


def test_validate_output_rejects_empty_refusal_and_preamble() -> None:
    """Reject output that cannot be safely returned as a rewrite."""

    with pytest.raises(RewriteValidationError):
        validate_output(" ")
    with pytest.raises(RewriteValidationError):
        validate_output("Sorry, ik kan deze tekst niet herschrijven.")
    with pytest.raises(RewriteValidationError):
        validate_output("Hier is de herschreven tekst: Vul het formulier in.")


def test_extract_usage_reads_langchain_metadata() -> None:
    """Read token counts from the raw LangChain response when available."""

    class RawResponse:
        usage_metadata = {"input_tokens": 12, "output_tokens": 7}

    usage = extract_usage(
        {"raw": RawResponse(), "parsed": {"generated_text": "Tekst"}},
        1.25,
    )

    assert usage.tokens_in == 12
    assert usage.tokens_out == 7
    assert usage.duration == 1.25


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

    rewrite, usage = result

    assert rewrite.generated_text == "Vul het formulier in."
    assert usage.tokens_in == 0
    assert usage.tokens_out == 0