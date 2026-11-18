"""Tests for configuration loading, enrichment and environment overrides."""

from pathlib import Path

from src.config import load_config


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------


def test_config_loads_guidelines(tmp_path: Path) -> None:
    """Load configured guidelines and environment overrides."""

    config_path = tmp_path / "config.json"
    config_path.write_text(
        """
        {
          "llm": {
            "provider": "openai"
          },
          "rewrite": {
            "guidelines": "Gebruik actieve zinnen."
          }
        }
        """,
        encoding="utf-8",
    )

    config = load_config(
        config_path,
        {
            "LLM__MODEL_NAME": "test-model",
        },
    )

    assert config.llm.model_name == "test-model"
    assert config.rewrite.guidelines == "Gebruik actieve zinnen."

