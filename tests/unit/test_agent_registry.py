"""Tests for startup triplestore readiness and provenance registration."""

import hashlib
import json

import src.agent_registry as registry


def test_wait_for_triplestore_retries_without_sudo(monkeypatch):
    """Poll until a non-sudo query returns data, retrying on errors and empty results."""

    calls = []
    responses = [
        ConnectionError("database down"),
        {"results": {"bindings": []}},
        {"results": {"bindings": [{"s": {"value": "http://example.org/s"}}]}},
    ]

    def fake_sparql(query, **kwargs):
        calls.append(kwargs)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(registry, "_sparql", fake_sparql)
    monkeypatch.setattr(registry.time, "sleep", lambda seconds: None)

    registry.wait_for_triplestore()

    assert calls == [{}, {}, {}]


def test_write_agent_info_reuses_existing_config_and_agent(monkeypatch):
    """Avoid duplicate RDF resources when the hash and agent already exist."""

    config = {
        "compose": {"image": "example/rewrite:1"},
        "config": {"/config/config.json": '{"llm": {"model": "test"}}'},
    }
    expected_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, indent=2).encode()
    ).hexdigest()
    calls = []

    def fake_sparql(query, **kwargs):
        calls.append((query, kwargs))
        if "SELECT ?agent_config" in query:
            assert expected_hash in query
            return {
                "results": {
                    "bindings": [{"agent_config": {"value": "http://example.org/config/1"}}]
                }
            }
        return {
            "results": {
                "bindings": [{"agent_uri": {"value": "http://example.org/agent/1"}}]
            }
        }

    monkeypatch.setattr(registry, "fetch_config", lambda: config)
    monkeypatch.setattr(registry, "_sparql", fake_sparql)
    monkeypatch.setattr(registry, "_config_uri", None)
    monkeypatch.setattr(registry, "_agent_uri", None)

    registry.write_agent_info("http://example.org/service")

    assert registry.get_agent_uri() == "http://example.org/agent/1"
    assert len(calls) == 2
    assert all(kwargs == {"sudo": True} for _, kwargs in calls)


def test_write_agent_info_inserts_compatible_config_and_agent(monkeypatch):
    """Write the v0.3.4 config-part and pipeline-component RDF shape."""

    config = {
        "compose": {"image": "example/rewrite:1"},
        "config": {"/config/config.json": '{"llm": {"model": "test"}}'},
    }
    calls = []

    def fake_sparql(query, **kwargs):
        calls.append((query, kwargs))
        if kwargs.get("update"):
            return {}
        return {"results": {"bindings": []}}

    monkeypatch.setattr(registry, "fetch_config", lambda: config)
    monkeypatch.setattr(registry, "_sparql", fake_sparql)
    monkeypatch.setattr(registry, "_config_uri", None)
    monkeypatch.setattr(registry, "_agent_uri", None)

    registry.write_agent_info("http://example.org/service")

    updates = [query for query, kwargs in calls if kwargs.get("update")]
    assert any("a ext:AgentConfig" in query and "ext:configHash" in query for query in updates)
    assert any("a ext:ConfigPart" in query and "ext:hasConfigFile" in query for query in updates)
    assert any(
        "a tcs:InstancePipelineComponent, foaf:Agent" in query
        and "prov:specializationOf <http://example.org/service>" in query
        and "ext:hasConfig" in query
        for query in updates
    )
    assert all(kwargs.get("sudo") is True for _, kwargs in calls)
    assert registry.get_agent_uri().startswith(registry.BASE_AGENT_URI)