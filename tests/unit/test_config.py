"""Tests for configuration loading, enrichment and environment overrides."""

from pathlib import Path

from src.config import load_config


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------


def test_config_loads_default_guideline_uri(tmp_path: Path) -> None:
    """Load the default URI and environment overrides."""

    config_path = tmp_path / "config.json"
    config_path.write_bytes(b'{"llm": {"provider": "openai"}}')

    config = load_config(
        config_path,
        {
            "LLM__MODEL_NAME": "test-model",
        },
    )

    assert config.llm.model_name == "test-model"
    assert config.rewrite.guideline_uri == "https://example.org/guidelines/hh/v3"

