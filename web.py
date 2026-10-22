"""HTTP boundary for synchronous HH rewrites.

The mu-python-template owns application assembly and the request context. This
module defines startup registration plus the rewrite service's router, request
mapping, dependency adapters and endpoint orchestration.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from decide_ai_service_base.util import (
    app,
    get_agent_uri,
    wait_for_triplestore,
    write_agent_info,
)

from src.config import ServiceConfig, get_config
from src.rewrite import (
    make_llm,
    rewrite_text,
)
from src.schema import (
    ActivityAttributes,
    AttemptAttributes,
    Resource,
    RewriteResponse,
)
from src.store import Store


# ---------------------------------------------------------------------------
# Module configuration
# ---------------------------------------------------------------------------

logger = logging.getLogger("hh-rewrite-service")
router = APIRouter()
AGENT_URI = "http://lblod.data.gift/id/components/hh-rewrite/v1.0.0"

JSON_API_MEDIA_TYPE = "application/vnd.api+json"
RELATIONSHIP_SCHEMA = {
    "type": "object",
    "properties": {
        "data": {
            "oneOf": [
                {
                    "type": "object",
                    "required": ["type", "id"],
                    "properties": {
                        "type": {"type": "string"},
                        "id": {"type": "string"},
                    },
                },
                {"type": "null"},
            ],
        }
    },
}
REQUEST_BODY_SCHEMA = {
    "type": "object",
    "required": ["data"],
    "properties": {
        "data": {
            "type": "object",
            "required": ["type", "attributes"],
            "properties": {
                "type": {
                    "type": "string",
                    "example": "generation-attempts",
                },
                "attributes": {
                    "type": "object",
                    "required": ["original-text", "related-subject", "related-field"],
                    "properties": {
                        "original-text": {
                            "type": "string",
                            "example": "De aanvrager dient het formulier in te vullen.",
                        },
                        "related-subject": {
                            "type": "string",
                            "format": "uri",
                            "example": "http://data.lblod.info/id/procedures/456",
                        },
                        "related-field": {
                            "type": "string",
                            "example": "dct:title",
                        },
                    },
                },
                "relationships": {
                    "type": "object",
                    "required": ["public-service"],
                    "properties": {
                        "public-service": RELATIONSHIP_SCHEMA,
                        "field-generation": RELATIONSHIP_SCHEMA,
                        "based-on": RELATIONSHIP_SCHEMA,
                        "guideline": RELATIONSHIP_SCHEMA,
                    },
                },
            },
        }
    },
}


@app.on_event("startup")
async def startup_event():
    wait_for_triplestore()
    write_agent_info(AGENT_URI)


def _new_uri(resource_type: str) -> str:
    """Create a new URI in the shared data namespace."""

    return f"http://data.lblod.info/id/{resource_type}/{uuid.uuid4()}"


def _iso(value: datetime) -> str:
    """Serialize a timestamp in the UTC format used by JSON:API responses."""

    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Template-provided service dependencies
# ---------------------------------------------------------------------------


def get_llm(config: ServiceConfig = Depends(get_config)) -> Any:
    """FastAPI dependency that creates the configured language-model client."""

    return make_llm(config)


def get_store(config: ServiceConfig = Depends(get_config)) -> Store:
    """FastAPI dependency that builds the SPARQL store."""

    return Store(
        domain_graph=config.graphs.domain,
        provenance_graph=config.graphs.provenance,
    )


def get_agent() -> str:
    """Return the versioned agent registered by the startup hook."""

    return get_agent_uri()


# ---------------------------------------------------------------------------
# Rewrite endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/hh-rewrites",
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                JSON_API_MEDIA_TYPE: {
                    "schema": REQUEST_BODY_SCHEMA,
                    "example": {
                        "data": {
                            "type": "generation-attempts",
                            "attributes": {
                                "original-text": "De aanvrager dient het formulier in te vullen.",
                                "related-subject": "http://data.lblod.info/id/procedures/456",
                                "related-field": "dct:title",
                            },
                            "relationships": {
                                "public-service": {
                                    "data": {
                                        "type": "public-services",
                                        "id": "123",
                                    }
                                },
                                "field-generation": {"data": None},
                                "based-on": {"data": None},
                                "guideline": {"data": None},
                            },
                        }
                    },
                }
            },
        }
    },
)
async def create_rewrite(
    request: Request,
    llm: Any = Depends(get_llm),
    store: Store = Depends(get_store),
    config: ServiceConfig = Depends(get_config),
    agent_uri: str = Depends(get_agent),
) -> JSONResponse:
    """Run one rewrite, persist it and return the generated attempt."""

    try:
        data = (await request.json())["data"]
        attributes = data["attributes"]
        relationships = data.get("relationships", {})
        original_text = attributes["original-text"]
        related_subject = attributes["related-subject"]
        related_field = attributes["related-field"]

        def resource_uri(name: str, resource_type: str) -> str | None:
            relationship = relationships.get(name, {}).get("data")
            if relationship is None:
                return None
            identifier = relationship["id"]
            return identifier if identifier.startswith("http") else (
                f"http://data.lblod.info/id/{resource_type}/{identifier}"
            )

        guideline_relationship = relationships.get("guideline", {}).get("data")
        if guideline_relationship is None:
            guideline_uri = config.rewrite.guideline_uri
        else:
            guideline_uri = guideline_relationship["id"]
            if not urlsplit(guideline_uri).scheme:
                return JSONResponse(
                    status_code=400,
                    content={
                        "errors": [
                            {
                                "status": "400",
                                "code": "invalid_guideline_reference",
                                "detail": "Guideline relationship id must be an absolute URI",
                            }
                        ]
                    },
                    media_type=JSON_API_MEDIA_TYPE,
                )

        guideline_text = store.read_guideline(guideline_uri)
        if not guideline_text or not guideline_text.strip():
            return JSONResponse(
                status_code=404,
                content={
                    "errors": [
                        {
                            "status": "404",
                            "code": "guideline_not_found",
                            "detail": "Guideline not found",
                        }
                    ]
                },
                media_type=JSON_API_MEDIA_TYPE,
            )

        public_service = resource_uri("public-service", "public-services")
        requested_generation = resource_uri("field-generation", "field-generations")
        based_on_uri = resource_uri("based-on", "generation-attempts")

        # Reuse a requested generation, or create the generation for this field.
        if requested_generation:
            field_generation_uri = requested_generation
        else:
            field_generation_uri = store.find_or_create_field_generation(
                related_subject, related_field, public_service
            )

        # A based-on attempt must belong to the same field generation.
        if based_on_uri:
            store.load_attempt(based_on_uri)
        address_form = store.read_language_variant(public_service)
        attempt_number = store.next_attempt_number(field_generation_uri)

        # Only the model invocation is provider-dependent; persistence remains deterministic.
        started_at = datetime.now(timezone.utc)
        result = await rewrite_text(
            config,
            llm,
            field=related_field,
            address_form=address_form,
            guideline_text=guideline_text,
            text=original_text,
        )
        ended_at = datetime.now(timezone.utc)

        # Store the generated text and provenance as one domain operation.
        attempt_uri = _new_uri("generation-attempts")
        activity_uri = _new_uri("generation-activities")

        store.write_attempt(
            attempt_uri=attempt_uri,
            field_generation_uri=field_generation_uri,
            attempt_number=attempt_number,
            original_text=original_text,
            generated_text=result.generated_text,
            activity_uri=activity_uri,
            started_at=started_at,
            ended_at=ended_at,
            address_form=address_form,
            guideline_uri=guideline_uri,
            agent_uri=agent_uri,
            based_on_uri=based_on_uri,
        )
        
        based_on_relationship = (
            {"data": {"type": "generation-attempts", "id": based_on_uri}}
            if based_on_uri
            else {"data": None}
        )
        response = RewriteResponse(
            data=Resource(
                type="generation-attempts",
                id=attempt_uri,
                attributes=AttemptAttributes(
                    uri=attempt_uri,
                    **{
                        "original-text": original_text,
                        "generated-text": result.generated_text,
                        "attempt-number": attempt_number,
                        "status": "completed",
                    },
                ).model_dump(by_alias=True),
                relationships={
                    "field-generation": {"data": {"type": "field-generations", "id": field_generation_uri}},
                    "based-on": based_on_relationship,
                    "generated-by": {"data": {"type": "generation-activities", "id": activity_uri}},
                },
            ),
            included=[
                Resource(
                    type="generation-activities",
                    id=activity_uri,
                    attributes=ActivityAttributes(
                        uri=activity_uri,
                        **{
                            "started-at": _iso(started_at),
                            "ended-at": _iso(ended_at),
                            "model": config.llm.model_name,
                            "address-form": address_form,
                            "language": "nl",
                            "status": "completed",
                        },
                    ).model_dump(by_alias=True),
                    relationships={
                        "guideline": {
                            "data": {"type": "guidelines", "id": guideline_uri}
                        },
                        "agent": {
                            "data": {"type": "agents", "id": agent_uri}
                        }
                    },
                )
            ],
        )
        return JSONResponse(
            status_code=201,
            content=response.model_dump(),
            media_type=JSON_API_MEDIA_TYPE,
        )
    except Exception:
        logger.exception("Rewrite request failed")
        return JSONResponse(status_code=500, content={"error": "rewrite failed"})

