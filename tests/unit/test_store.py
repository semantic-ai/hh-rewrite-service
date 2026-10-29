"""Tests for injected SPARQL escaping and provenance persistence."""

from datetime import datetime, timezone

import src.store as store_module
from src.store import Store


# ---------------------------------------------------------------------------
# Test doubles for template escape helpers
# ---------------------------------------------------------------------------


def sparql_escape(value):
    """Match the template helper signature for injected string escaping."""

    return '"""' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"""'


def sparql_escape_uri(value):
    """Match the template helper signature for injected IRI escaping."""

    return "<" + str(value).replace("\\", "\\\\").replace('"', '\\"') + ">"


# ---------------------------------------------------------------------------
# Persistence behavior
# ---------------------------------------------------------------------------


def test_write_generation_activity_uses_one_insert_and_preserves_literals(monkeypatch) -> None:
    """Write domain and provenance data through one update operation."""

    updates = []
    monkeypatch.setattr(store_module, "template_query", lambda query: [])
    monkeypatch.setattr(store_module, "template_update", updates.append)
    monkeypatch.setattr(store_module, "sparql_escape", sparql_escape)
    monkeypatch.setattr(store_module, "sparql_escape_uri", sparql_escape_uri)
    store = Store()

    store.write_generation_activity(
        activity_uri="http://example.org/activity/1",
        field_generation_uri="http://example.org/generation/1",
        attempt_number=1,
        original_text='Origineel "tekst" \\"',
        generated_text="Nieuwe tekst",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        address_form="nl-be-x-formal",
        guideline_uri="http://example.org/guideline/1",
        agent_uri="http://example.org/agent/1",
    )

    assert len(updates) == 1
    assert updates[0].startswith("INSERT DATA")
    assert "a <http://www.w3.org/ns/prov#Activity>" in updates[0]
    assert "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#GenerationActivity" in updates[0]
    assert "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#notUsed" in updates[0]
    assert "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#hasGenerationActivity" in updates[0]
    assert 'Origineel \\\"tekst\\\" \\\\\\"' in updates[0]
    assert "hasConfig" not in updates[0]
    assert "configHash" not in updates[0]


def test_read_guideline_queries_typed_resource(monkeypatch) -> None:
    """Read guideline text from the configured domain graph."""

    queries = []
    monkeypatch.setattr(
        store_module,
        "template_query",
        lambda query: queries.append(query) or {
            "results": {
                "bindings": [
                    {
                        "uuid": {"value": "hh-v1"},
                        "title": {"value": "Heldere Overheid v1"},
                        "text": {"value": "Gebruik actieve zinnen."},
                    }
                ]
            }
        },
    )
    monkeypatch.setattr(store_module, "sparql_escape_uri", sparql_escape_uri)
    store = Store()

    assert store.read_guideline("http://example.org/guideline/1") == "Gebruik actieve zinnen."
    assert len(queries) == 1
    assert "<http://mu.semte.ch/vocabularies/ext/Guideline>" in queries[0]
    assert "<http://mu.semte.ch/vocabularies/ext/guidelineText>" in queries[0]


def test_resolve_by_uuid_filters_rdf_type(monkeypatch) -> None:
    """Resolve only a resource with the requested RDF type."""

    queries = []
    monkeypatch.setattr(
        store_module,
        "template_query",
        lambda query: queries.append(query)
        or {"results": {"bindings": [{"resource": {"value": "http://example.org/service/1"}}]}},
    )
    monkeypatch.setattr(store_module, "sparql_escape", sparql_escape)
    monkeypatch.setattr(store_module, "sparql_escape_uri", sparql_escape_uri)
    store = Store()

    assert (
        store.resolve_by_uuid(
            "123",
            "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#InstancePublicService",
        )
        == "http://example.org/service/1"
    )
    assert " a <https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#InstancePublicService>" in queries[0]


def test_resolve_by_uri_returns_none_when_not_visible(monkeypatch) -> None:
    """Treat a URI hidden by authorization as an unknown resource."""

    queries = []
    monkeypatch.setattr(
        store_module,
        "template_query",
        lambda query: queries.append(query) or {"results": {"bindings": []}},
    )
    monkeypatch.setattr(store_module, "sparql_escape_uri", sparql_escape_uri)

    assert (
        Store().resolve_by_uri(
            "http://example.org/service/1",
            "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#InstancePublicService",
        )
        is None
    )
    assert "VALUES ?resource { <http://example.org/service/1> }" in queries[0]
    assert "?resource a <https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#InstancePublicService>" in queries[0]


def test_read_subject_types_returns_all_types(monkeypatch) -> None:
    """Return every RDF type of the subject that owns the field."""

    monkeypatch.setattr(
        store_module,
        "template_query",
        lambda query: {
            "results": {
                "bindings": [
                    {"type": {"value": "http://data.europa.eu/m8g/Requirement"}},
                    {"type": {"value": "http://example.org/Other"}},
                ]
            }
        },
    )
    monkeypatch.setattr(store_module, "sparql_escape_uri", sparql_escape_uri)

    assert Store().read_subject_types("http://example.org/requirement/1") == [
        "http://data.europa.eu/m8g/Requirement",
        "http://example.org/Other",
    ]


def test_write_generation_activity_links_aicall_to_activity(monkeypatch) -> None:
    """Persist model usage as one AICall linked from the activity."""

    updates = []
    monkeypatch.setattr(store_module, "template_update", updates.append)
    monkeypatch.setattr(store_module, "sparql_escape", sparql_escape)
    monkeypatch.setattr(store_module, "sparql_escape_float", lambda value: str(value))
    monkeypatch.setattr(store_module, "sparql_escape_int", lambda value: str(value))
    monkeypatch.setattr(store_module, "sparql_escape_uri", sparql_escape_uri)
    store = Store()

    store.write_generation_activity(
        activity_uri="http://example.org/activity/1",
        field_generation_uri="http://example.org/generation/1",
        attempt_number=1,
        original_text="Origineel",
        generated_text="Nieuwe tekst",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
        address_form="nl-be-x-formal",
        guideline_uri="http://example.org/guideline/1",
        agent_uri="http://example.org/agent/1",
        model_uri="http://data.lblod.info/ontology/airo#mistral-small-2603",
        endpoint="https://api.mistral.ai/v1",
        tokens_in=12,
        tokens_out=7,
        duration=1.25,
    )

    assert len(updates) == 1
    assert "performedAICall" in updates[0]
    assert "AICall" in updates[0]
    assert "aiModel" in updates[0]
    assert "tokenIn" in updates[0]
    assert "tokenOut" in updates[0]
    assert "duration" in updates[0]
