"""HTTP boundary for synchronous HH rewrites.

The mu-python-template owns application assembly and the request context. This
module defines startup registration plus the rewrite service's router, request
mapping, dependency adapters and endpoint orchestration.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from decide_ai_service_base.ai_logging import model_name_to_uri
from decide_ai_service_base.util import (
    app,
    get_agent_uri,
    wait_for_triplestore,
    write_agent_info,
)

from src.config import ServiceConfig, get_config
from src.rewrite import (
    RewriteProviderError,
    RewriteValidationError,
    make_llm,
    rewrite_text,
)
from src.schema import (
    ActivityAttributes,
    Resource,
    RewriteRequest,
    RewriteResponse,
)
from pydantic import ValidationError
from src.store import Store


# ---------------------------------------------------------------------------
# Module configuration
# ---------------------------------------------------------------------------

logger = logging.getLogger("hh-rewrite-service")
router = APIRouter()
AGENT_URI = "http://lblod.data.gift/id/components/hh-rewrite/v1.0.0"

JSON_API_MEDIA_TYPE = "application/vnd.api+json"

RESOURCE_RDF_TYPES = {
    "public_service": "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#InstancePublicService",
    "field_generation": "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#FieldGeneration",
}

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
                    "example": "generation-activities",
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
                            "format": "uri",
                            "example": "http://purl.org/dc/terms/title",
                        },
                    },
                },
                "relationships": {
                    "type": "object",
                    "required": ["public-service"],
                    "properties": {
                        "public-service": RELATIONSHIP_SCHEMA,
                        "field-generation": RELATIONSHIP_SCHEMA,
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


def _error(status_code: int, code: str, detail: str) -> JSONResponse:
    """Return one JSON:API error response."""

    return JSONResponse(
        status_code=status_code,
        content={
            "errors": [
                {
                    "status": str(status_code),
                    "code": code,
                    "title": code.replace("_", " ").title(),
                    "detail": detail,
                }
            ]
        },
        media_type=JSON_API_MEDIA_TYPE,
    )


# ---------------------------------------------------------------------------
# Template-provided service dependencies
# ---------------------------------------------------------------------------


def get_llm(config: ServiceConfig = Depends(get_config)) -> Any:
    """FastAPI dependency that creates the configured language-model client."""

    return make_llm(config)


def get_store() -> Store:
    """FastAPI dependency that builds the SPARQL store."""

    return Store()


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
                            "type": "generation-activities",
                            "attributes": {
                                "original-text": "De aanvrager dient het formulier in te vullen.",
                                "related-subject": "http://data.lblod.info/id/procedures/456",
                                "related-field": "http://purl.org/dc/terms/title",
                            },
                            "relationships": {
                                "public-service": {
                                    "data": {
                                        "type": "public-services",
                                        "id": "123",
                                    }
                                },
                                "field-generation": {"data": None},
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
    """Run one rewrite, persist it and return the generated activity."""

    try:
        
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != JSON_API_MEDIA_TYPE:
            return _error(415, "unsupported_media_type", "Content-Type must be application/vnd.api+json")
 
        try:
            payload = RewriteRequest.model_validate(await request.json())
        except (ValueError, ValidationError):
            logger.debug("Invalid rewrite request")
            return _error(400, "invalid_request", "Request body is not valid JSON:API")

        if payload.data.type != "generation-activities":
            return _error(400, "invalid_request", "data.type must be generation-activities")

        attributes = payload.data.attributes
        relationships = payload.data.relationships
        original_text = attributes.original_text
        related_subject = attributes.related_subject
        related_field = attributes.related_field

        parsed_related_field = urlsplit(related_field)
        if parsed_related_field.scheme not in {"http", "https"} or not parsed_related_field.netloc:
            return _error(
                400,
                "invalid_related_field",
                "related-field must be an absolute predicate URI",
            )

        if not original_text.strip():
            return _error(422, "empty_text", "original-text must not be empty")
        if len(original_text) > config.limits.max_input_length:
            return _error(413, "input_too_large", "original-text exceeds the configured maximum length")

        public_service_relationship = relationships.public_service.data
        if public_service_relationship is None:
            return _error(422, "public_service_required", "public-service relationship is required")

        def resource_uri(name: str) -> str | None:
            relationship = getattr(relationships, name).data
            if relationship is None:
                return None
            identifier = relationship.id
            parsed_identifier = urlsplit(identifier)
            if parsed_identifier.scheme in {"http", "https"} and parsed_identifier.netloc:
                return identifier
            return store.resolve_by_uuid(identifier, RESOURCE_RDF_TYPES[name])

        guideline_relationship = relationships.guideline.data
        if guideline_relationship is None:
            guideline_uri = config.rewrite.guideline_uri
        else:
            guideline_uri = guideline_relationship.id
            parsed_guideline = urlsplit(guideline_uri)
            if parsed_guideline.scheme not in {"http", "https"} or not parsed_guideline.netloc:
                return _error(
                    400,
                    "invalid_guideline_reference",
                    "Guideline relationship id must be an absolute URI",
                )

        guideline_text = store.read_guideline(guideline_uri)
        if not guideline_text or not guideline_text.strip():
            return _error(404, "guideline_not_found", "Guideline not found")

        public_service = resource_uri("public_service")
        if public_service is None:
            return _error(404, "public_service_not_found", "Public service not found")
        requested_generation = resource_uri("field_generation")

        if relationships.field_generation.data is not None and requested_generation is None:
            return _error(404, "field_generation_not_found", "Field generation not found")

        # Reuse a requested generation, or create the generation for this field.
        if requested_generation:
            field_generation_uri = requested_generation
        else:
            field_generation_uri = store.find_or_create_field_generation(
                related_subject, related_field, public_service
            )

        address_form = store.read_language_variant(public_service) or "nl-be-x-formal"
        attempt_number = store.next_attempt_number(field_generation_uri)
        if attempt_number > config.limits.max_attempts:
            return _error(409, "attempt_limit_reached", "Maximum attempts reached for this field")

        # Only the model invocation is provider-dependent; persistence remains deterministic.
        started_at = datetime.now(timezone.utc)
        result, usage = await rewrite_text(
            config,
            llm,
            field=related_field,
            address_form=address_form,
            guideline_text=guideline_text,
            text=original_text,
        )
        ended_at = datetime.now(timezone.utc)

        # Store the generated text and provenance as one domain operation.
        activity_uri = _new_uri("generation-activities")

        store.write_generation_activity(
            activity_uri=activity_uri,
            field_generation_uri=field_generation_uri,
            attempt_number=attempt_number,
            original_text=original_text,
            generated_text=result.generated_text,
            started_at=started_at,
            ended_at=ended_at,
            address_form=address_form,
            guideline_uri=guideline_uri,
            agent_uri=agent_uri,
            model_uri=model_name_to_uri(config.llm.model_name),
            endpoint=config.llm.base_url or config.llm.provider,
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            duration=usage.duration,
        )
        
        response = RewriteResponse(
            data=Resource(
                type="generation-activities",
                id=activity_uri,
                attributes=ActivityAttributes(
                    uri=activity_uri,
                    **{
                        "attempt-number": attempt_number,
                        "original-text": original_text,
                        "generated-text": result.generated_text,
                        "started-at": _iso(started_at),
                        "ended-at": _iso(ended_at),
                        "model": config.llm.model_name,
                        "address-form": address_form,
                        "language": "nl",
                        "status": "notUsed",
                    },
                ).model_dump(by_alias=True),
                relationships={
                    "field-generation": {"data": {"type": "field-generations", "id": field_generation_uri}},
                    "guideline": {
                        "data": {"type": "guidelines", "id": guideline_uri}
                    },
                    "agent": {
                        "data": {"type": "agents", "id": agent_uri}
                    },
                },
            ),
            included=[],
        )
        return JSONResponse(
            status_code=201,
            content=response.model_dump(),
            media_type=JSON_API_MEDIA_TYPE,
        )
    except (RewriteProviderError, RewriteValidationError):
        logger.warning("Rewrite model call failed")
        return _error(504, "model_provider_error", "The rewrite model could not produce a response")
    except Exception:
        logger.error("Rewrite request failed")
        return _error(500, "rewrite_failed", "The rewrite request failed")


app.include_router(router)

