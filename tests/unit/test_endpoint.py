"""Tests for the JSON:API rewrite endpoint."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import ServiceConfig
from web import get_agent, get_config, get_llm, get_store, router


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------


PUBLIC_SERVICE = "http://data.lblod.info/id/public-services/123"
RELATED_SUBJECT = "http://data.lblod.info/id/procedures/456"
FIELD_GENERATION = "http://data.lblod.info/id/field-generations/1"
PREVIOUS_ATTEMPT = "http://data.lblod.info/id/generation-attempts/previous"
service_app = FastAPI()
service_app.include_router(router)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeLlm:
    """Configurable model double used to test success and provider failures."""

    def __init__(self, response=None, error=None):
        self.response = response or {"generated_text": "Vul het formulier in."}
        self.error = error
        self.calls = 0
        self.messages = []

    async def ainvoke(self, messages):
        self.calls += 1
        self.messages.append(messages)
        if self.error:
            raise self.error
        return self.response


class FakeStore:
    """In-memory store double that records attempted persistence operations."""

    def __init__(self, *, attempt_number=1, field_generation=None, predecessor=None):
        self.attempt_number = attempt_number
        self.field_generation = field_generation
        self.predecessor = predecessor
        self.writes = []
        self.guidelines = {
            ServiceConfig().rewrite.guideline_uri: "Gebruik actieve zinnen."
        }
        self.guideline_reads = []

    def read_guideline(self, guideline_uri):
        self.guideline_reads.append(guideline_uri)
        return self.guidelines.get(guideline_uri)

    def find_or_create_field_generation(self, related_subject, related_field, public_service):
        return FIELD_GENERATION

    def load_attempt(self, attempt_uri):
        return self.predecessor

    def read_language_variant(self, public_service_uri):
        return "nl-be-x-formal"

    def next_attempt_number(self, field_generation_uri):
        return self.attempt_number

    def write_attempt(self, **values):
        self.writes.append(values)

# ---------------------------------------------------------------------------
# Fixtures and request construction
# ---------------------------------------------------------------------------


def request_payload(**overrides):
    """Build a valid request payload with optional attribute overrides."""

    attributes = {
        "original-text": "De aanvrager dient het formulier in te vullen.",
        "related-subject": RELATED_SUBJECT,
        "related-field": "dct:title",
    }
    attributes.update(overrides.pop("attributes", {}))
    relationships = {
        "public-service": {"data": {"type": "public-services", "id": "123"}},
    }
    relationships.update(overrides.pop("relationships", {}))
    return {
        "data": {
            "type": "generation-attempts",
            "attributes": attributes,
            "relationships": relationships,
        }
    }


@pytest.fixture
def configured_client():
    """Provide a test client with configuration, model and store overrides."""

    config = ServiceConfig()
    llm = FakeLlm()
    store = FakeStore()
    service_app.dependency_overrides[get_config] = lambda: config
    service_app.dependency_overrides[get_llm] = lambda: llm
    service_app.dependency_overrides[get_store] = lambda: store
    service_app.dependency_overrides[get_agent] = lambda: "http://example.org/agent/1"
    with TestClient(service_app) as client:
        yield client, llm, store
    service_app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Rewrite endpoint behavior
# ---------------------------------------------------------------------------


def test_post_rewrite_returns_attempt_and_activity(configured_client):
    """Return the completed attempt and included provenance activity."""

    client, llm, store = configured_client

    response = client.post(
        "/hh-rewrites",
        json=request_payload(),
        headers={"content-type": "application/vnd.api+json"},
    )

    body = response.json()
    assert response.status_code == 201
    assert response.headers["content-type"].startswith("application/vnd.api+json")
    assert body["data"]["type"] == "generation-attempts"
    assert body["data"]["attributes"]["generated-text"] == "Vul het formulier in."
    assert body["included"][0]["type"] == "generation-activities"
    assert len(store.writes) == 1
    assert llm.calls == 1
    assert store.guideline_reads == [ServiceConfig().rewrite.guideline_uri]
    assert store.writes[0]["guideline_uri"] == ServiceConfig().rewrite.guideline_uri
    assert store.writes[0]["model_uri"].endswith("#gpt-6-luna")
    assert store.writes[0]["endpoint"] == "openai"
    assert "Gebruik actieve zinnen." in llm.messages[0][1]["content"]


def test_post_rewrite_uses_request_guideline_uri(configured_client):
    """Use a request-specific guideline for the prompt and provenance."""

    client, llm, store = configured_client
    guideline_uri = "https://example.org/guidelines/hh/v2"
    store.guidelines[guideline_uri] = "Gebruik de actieve aanspreekvorm."

    response = client.post(
        "/hh-rewrites",
        json=request_payload(
            relationships={
                "guideline": {
                    "data": {"type": "guidelines", "id": guideline_uri}
                }
            }
        ),
        headers={"content-type": "application/vnd.api+json"},
    )

    assert response.status_code == 201
    assert store.guideline_reads == [guideline_uri]
    assert store.writes[0]["guideline_uri"] == guideline_uri
    assert response.json()["included"][0]["relationships"]["guideline"]["data"]["id"] == guideline_uri
    assert "Gebruik de actieve aanspreekvorm." in llm.messages[0][1]["content"]


def test_missing_guideline_returns_404_without_model_or_write(configured_client):
    """Reject a missing guideline before invoking the model or creating data."""

    client, llm, store = configured_client
    guideline_uri = "https://example.org/guidelines/missing"

    response = client.post(
        "/hh-rewrites",
        json=request_payload(
            relationships={
                "guideline": {
                    "data": {"type": "guidelines", "id": guideline_uri}
                }
            }
        ),
        headers={"content-type": "application/vnd.api+json"},
    )

    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "guideline_not_found"
    assert store.guideline_reads == [guideline_uri]
    assert llm.calls == 0
    assert not store.writes


def test_short_guideline_id_returns_400_without_lookup(configured_client):
    """Reject a guideline relationship that is not an absolute URI."""

    client, llm, store = configured_client

    response = client.post(
        "/hh-rewrites",
        json=request_payload(
            relationships={
                "guideline": {
                    "data": {"type": "guidelines", "id": "v2"}
                }
            }
        ),
        headers={"content-type": "application/vnd.api+json"},
    )

    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "invalid_guideline_reference"
    assert not store.guideline_reads
    assert llm.calls == 0


def test_openapi_describes_json_api_request_body():
    """Expose the JSON:API document in Swagger without changing parsing behavior."""

    operation = service_app.openapi()["paths"]["/hh-rewrites"]["post"]
    request_body = operation["requestBody"]
    media_type = request_body["content"]["application/vnd.api+json"]

    assert request_body["required"] is True
    assert media_type["schema"]["required"] == ["data"]
    assert media_type["example"]["data"]["relationships"]["public-service"]["data"]["id"] == "123"


@pytest.mark.parametrize(
    ("headers", "body", "status", "code"),
    [
        ({}, request_payload(), 415, "unsupported_media_type"),
        ({"content-type": "application/vnd.api+json"}, b"{", 400, "invalid_request"),
        (
            {"content-type": "application/vnd.api+json"},
            request_payload(attributes={"original-text": "  "}),
            422,
            "empty_text",
        ),
        (
            {"content-type": "application/vnd.api+json"},
            request_payload(attributes={"original-text": "<p>Vul het formulier in.</p>"}),
            422,
            "markup_not_allowed",
        ),
    ],
)
def test_post_rewrite_rejects_invalid_requests(configured_client, headers, body, status, code):
    """Return a JSON:API error and avoid persistence for invalid input."""

    client, _, store = configured_client

    request_kwargs = {"json": body} if isinstance(body, dict) else {"content": body}
    response = client.post("/hh-rewrites", headers=headers, **request_kwargs)

    assert response.status_code == status
    assert response.json()["errors"][0]["code"] == code
    assert not store.writes


def test_post_rewrite_rejects_unknown_based_on(configured_client):
    """Reject a predecessor reference that cannot be loaded."""

    client, _, store = configured_client
    store.predecessor = None

    response = client.post(
        "/hh-rewrites",
        json=request_payload(
            relationships={
                "public-service": {"data": {"type": "public-services", "id": "123"}},
                "based-on": {"data": {"type": "generation-attempts", "id": "missing"}},
            }
        ),
        headers={"content-type": "application/vnd.api+json"},
    )

    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "based_on_not_found"
    assert not store.writes


def test_post_rewrite_rejects_attempt_limit(configured_client):
    """Reject new attempts after the configured limit is reached."""

    client, _, store = configured_client
    store.attempt_number = ServiceConfig().limits.max_attempts + 1

    response = client.post(
        "/hh-rewrites",
        json=request_payload(),
        headers={"content-type": "application/vnd.api+json"},
    )

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "attempt_limit_reached"
    assert not store.writes


def test_model_failure_does_not_write_attempt(configured_client):
    """Map provider failure to a gateway error without writing an attempt."""

    client, _, store = configured_client
    service_app.dependency_overrides[get_llm] = lambda: FakeLlm(error=ConnectionError("offline"))

    response = client.post(
        "/hh-rewrites",
        json=request_payload(),
        headers={"content-type": "application/vnd.api+json"},
    )

    assert response.status_code == 504
    assert response.json()["errors"][0]["code"] == "model_provider_error"
    assert not store.writes


