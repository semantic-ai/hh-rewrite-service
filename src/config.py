"""Typed service configuration loaded from JSON and environment overrides."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Configuration models
# ---------------------------------------------------------------------------


class LLMConfig(BaseModel):
    """Settings for the provider-neutral language-model client."""

    provider: str = "openai"
    model_name: str = Field(default="gpt-6-luna", alias="model_name")
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = 0.1
    timeout: float = 30.0
    # Context window in tokens; only local providers (Ollama) accept it.
    num_ctx: int | None = None


class LimitsConfig(BaseModel):
    """Operational limits applied to input, attempts and retries."""

    max_input_length: int = 4000
    max_attempts: int = 20
    retries: int = 1


class PromptConfig(BaseModel):
    """System and user message templates used to assemble rewrite prompts."""

    system_message: str = (
        "Je bent een taaladviseur voor het lokaal bestuur. "
        "Je herschrijft en verbetert veldteksten uitsluitend op taalkundig, "
        "stilistisch en structureel niveau, volgens de Heerlijk Helder-richtlijnen. "
        "Behoud elk feit, elke datum, elk bedrag, elke voorwaarde en de juridische betekenis. "
        "Geef uitsluitend de herschreven tekst terug."
    )
    user_message: str = (
        "Veld: {field}\nAanhefvorm: {address_form}\nRichtlijnen:\n{guidelines}\n\n"
        "Tekst:\n{text}"
    )


class FieldLabel(BaseModel):
    """Readable field name for a predicate, optionally scoped to a subject type."""

    field: str
    label: str
    subject_type: str | None = None


LPDC = "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#"
DCT = "http://purl.org/dc/terms/"
M8G = "http://data.europa.eu/m8g/"
CPSV = "http://purl.org/vocab/cpsv#"


def _default_field_labels() -> list[FieldLabel]:
    """Map LPDC subject types and predicates to the field names used in the guidelines."""

    entries = [
        (f"{LPDC}InstancePublicService", f"{DCT}title", "Producttitel"),
        (f"{LPDC}InstancePublicService", f"{DCT}description", "Beschrijving"),
        (f"{LPDC}InstancePublicService", f"{LPDC}additionalDescription", "Aanvullende beschrijving"),
        (f"{LPDC}InstancePublicService", f"{LPDC}exception", "Uitzonderingen"),
        (f"{LPDC}InstancePublicService", f"{LPDC}regulation", "Regelgeving"),
        (f"{M8G}Requirement", f"{DCT}title", "Voorwaarden (tussentitel)"),
        (f"{M8G}Requirement", f"{DCT}description", "Voorwaarden"),
        (f"{M8G}Evidence", f"{DCT}title", "Bewijsstukken (tussentitel)"),
        (f"{M8G}Evidence", f"{DCT}description", "Bewijsstukken"),
        (f"{CPSV}Rule", f"{DCT}title", "Procedure (tussentitel)"),
        (f"{CPSV}Rule", f"{DCT}description", "Procedure"),
        (f"{M8G}Cost", f"{DCT}title", "Kosten (tussentitel)"),
        (f"{M8G}Cost", f"{DCT}description", "Kosten"),
        (f"{LPDC}FinancialAdvantage", f"{DCT}title", "Financiële voordelen (tussentitel)"),
        (f"{LPDC}FinancialAdvantage", f"{DCT}description", "Financiële voordelen"),
    ]
    return [
        FieldLabel(subject_type=subject_type, field=field, label=label)
        for subject_type, field, label in entries
    ]


class RewriteConfig(BaseModel):
    """Guideline identity, prompt configuration and prompt-facing labels."""

    guideline_uri: str = "https://example.org/guidelines/hh/v3"
    prompt: PromptConfig = Field(default_factory=PromptConfig)
    # Instance language variants mapped to the address form named in the guidelines.
    address_forms: dict[str, str] = Field(
        default_factory=lambda: {
            "nl": "u-vorm",
            "nl-be-x-formal": "u-vorm",
            "nl-be-x-informal": "je-vorm",
        }
    )
    field_labels: list[FieldLabel] = Field(default_factory=_default_field_labels)


class ServiceConfig(BaseModel):
    """Complete validated configuration used by the rewrite service."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    rewrite: RewriteConfig = Field(default_factory=RewriteConfig)


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------


def _parse_override(value: str) -> Any:
    """Parse an environment value as JSON, or keep it as a string."""

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _apply_environment_overrides(data: dict[str, Any], environ: dict[str, str]) -> None:
    """Apply double-underscore environment variables to nested config data."""

    for name, value in environ.items():
        if "__" not in name:
            continue
        path = name.lower().split("__")
        target: dict[str, Any] = data
        for key in path[:-1]:
            current = target.get(key)
            if not isinstance(current, dict):
                current = {}
                target[key] = current
            target = current
        target[path[-1]] = _parse_override(value)


def load_config(path: str | Path | None = None, environ: dict[str, str] | None = None) -> ServiceConfig:
    """Load and validate service configuration from disk and the environment."""

    config_path = Path(path or os.getenv("CONFIG_PATH", "/config/config.json"))
    if config_path.exists():
        data = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        data = {}
    _apply_environment_overrides(data, environ if environ is not None else os.environ)
    config = ServiceConfig.model_validate(data)
    return config


@lru_cache(maxsize=1)
def get_config() -> ServiceConfig:
    """Return the process-wide cached configuration for dependency injection."""

    return load_config()