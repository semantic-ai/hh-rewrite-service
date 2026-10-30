"""HTTP boundary for synchronous HH rewrites.

The mu-python-template owns application assembly and the request context. This
module defines startup registration plus the rewrite service's router, request
mapping, dependency adapters and endpoint orchestration.
"""

from __future__ import annotations

import builtins
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from flask import Blueprint, Response, request
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from src.agent_registry import (
    ALLOW_MU_AUTH_SUDO,
    get_agent_uri,
    model_name_to_uri,
    set_agent_uri,
    wait_for_triplestore,
    write_agent_info,
)
from src.config import ServiceConfig, get_config
from src.rewrite import (
    RewriteProviderError,
    RewriteValidationError,
    address_form_label,
    field_label,
    make_llm,
    rewrite_text,
)
from src.schema import (
    ActivityAttributes,
    Resource,
    RewriteRequest,
    RewriteResponse,
)

if TYPE_CHECKING:
    from src.store import Store


# ---------------------------------------------------------------------------
# Module configuration
# ---------------------------------------------------------------------------

logger = logging.getLogger("hh-rewrite-service")
bp = Blueprint("hh_rewrite", __name__)
AGENT_URI = "http://lblod.data.gift/id/components/hh-rewrite/v1.0.0"

JSON_API_MEDIA_TYPE = "application/vnd.api+json"

RESOURCE_RDF_TYPES = {
    "public_service": "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#InstancePublicService",
    "field_generation": "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#FieldGeneration",
}

def _startup() -> None:
    wait_for_triplestore()
    if ALLOW_MU_AUTH_SUDO:
        write_agent_info(AGENT_URI)
    else:
        # write_agent_info needs sudo to write the jobs graph; without it the
        # agent resource is seeded by a migration instead.
        set_agent_uri(AGENT_URI)


def _new_uri(resource_type: str) -> str:
    """Create a new URI in the shared data namespace."""

    return f"http://data.lblod.info/id/{resource_type}/{uuid.uuid4()}"


def _iso(value: datetime) -> str:
    """Serialize a timestamp in the UTC format used by JSON:API responses."""

    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_api(status_code: int, content: dict[str, Any]) -> Response:
    body = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return Response(body, status=status_code, mimetype=JSON_API_MEDIA_TYPE)


def _error(status_code: int, code: str, detail: str) -> Response:
    """Return one JSON:API error response."""

    return _json_api(
        status_code,
        {
            "errors": [
                {
                    "status": str(status_code),
                    "code": code,
                    "title": code.replace("_", " ").title(),
                    "detail": detail,
                }
            ]
        },
    )


@bp.app_errorhandler(HTTPException)
def _http_error(error: HTTPException) -> Response:
    """Return Flask routing errors such as 404 and 405 as JSON:API errors."""

    code = (error.name or "http_error").lower().replace(" ", "_")
    return _error(error.code or 500, code, error.description or "")


# ---------------------------------------------------------------------------
# Template-provided service dependencies
# ---------------------------------------------------------------------------


def get_llm() -> Any:
    """Create the configured language-model client."""

    return make_llm(get_config())


def get_store() -> Store:
    """Build the SPARQL store."""

    # Imported lazily: src.store needs the template's helpers modules, which
    # only exist inside the image, while endpoint tests import web without them.
    from src.store import Store

    return Store()


def get_agent() -> str:
    """Return the versioned agent registered by the startup hook."""

    return get_agent_uri()


# ---------------------------------------------------------------------------
# Rewrite endpoint
# ---------------------------------------------------------------------------


@bp.post("/hh-rewrites")
async def create_rewrite() -> Response:
    """Run one rewrite, persist it and return the generated activity."""

    try:
        llm = get_llm()
        store = get_store()
        config = get_config()
        agent_uri = get_agent()
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != JSON_API_MEDIA_TYPE:
            return _error(415, "unsupported_media_type", "Content-Type must be application/vnd.api+json")

        try:
            payload = RewriteRequest.model_validate(request.get_json(silent=True))
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
        if re.search(r"<\s*/?\s*[A-Za-z][^>]*>", original_text):
            return _error(422, "markup_not_allowed", "original-text must not contain markup")
        if len(original_text) > config.limits.max_input_length:
            return _error(413, "input_too_large", "original-text exceeds the configured maximum length")

        public_service_relationship = relationships.public_service.data
        if public_service_relationship is None:
            return _error(422, "public_service_required", "public-service relationship is required")

        def resource_uri(name: str) -> str | None:
            relationship = getattr(relationships, name).data
            if relationship is None:
                return None
            # Both forms resolve through authorization, which is the only access check.
            identifier = relationship.id
            parsed_identifier = urlsplit(identifier)
            if parsed_identifier.scheme in {"http", "https"} and parsed_identifier.netloc:
                return store.resolve_by_uri(identifier, RESOURCE_RDF_TYPES[name])
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

        # The prompt gets readable labels; provenance keeps the raw URI and variant.
        subject_types = store.read_subject_types(related_subject)
        prompt_field = field_label(config, subject_types, related_field)
        prompt_address_form = address_form_label(config, address_form)

        # Only the model invocation is provider-dependent; persistence remains deterministic.
        started_at = datetime.now(timezone.utc)
        result, usage = await rewrite_text(
            config,
            llm,
            field=prompt_field,
            address_form=prompt_address_form,
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
        return _json_api(201, response.model_dump())
    except (RewriteProviderError, RewriteValidationError):
        logger.warning("Rewrite model call failed")
        return _error(504, "model_provider_error", "The rewrite model could not produce a response")
    except Exception:
        logger.error("Rewrite request failed")
        return _error(500, "rewrite_failed", "The rewrite request failed")


# The template publishes its Flask app on builtins before importing this
# module. Without it (e.g. unit tests mounting bp on their own app) there is
# nothing to register on and no triplestore to wait for.
if hasattr(builtins, "app"):
    builtins.app.register_blueprint(bp)
    _startup()

