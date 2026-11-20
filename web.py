"""HTTP boundary for background HH rewrites.

The mu-python-template owns application assembly and the request context. This
module defines startup registration plus the rewrite service's router, request
mapping, dependency adapters and endpoint orchestration.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import logging
import uuid
from datetime import datetime, timezone
from threading import Thread
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from flask import Blueprint, Response, copy_current_request_context, request
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
from src.constants import (
    DATA_URI_BASE,
    INSTANCE_PUBLIC_SERVICE,
    JOB_URI_PREFIX,
    JOB_STATUS,
    TASK_OPERATION,
)
from src.config import get_config
from src.rewrite import (
    address_form_label,
    field_label,
    make_llm,
    rewrite_text,
)
from src.schema import RewriteRequest

if TYPE_CHECKING:
    from src.store import Store


# ---------------------------------------------------------------------------
# Module configuration
# ---------------------------------------------------------------------------

logger = logging.getLogger("hh-rewrite-service")
bp = Blueprint("hh_rewrite", __name__)
AGENT_URI = "http://lblod.data.gift/id/components/hh-rewrite/v1.0.0"


def _startup() -> None:
    wait_for_triplestore()
    if ALLOW_MU_AUTH_SUDO:
        write_agent_info(AGENT_URI)
    else:
        # write_agent_info needs sudo to write the jobs graph; without it the
        # agent resource is seeded by a migration instead.
        set_agent_uri(AGENT_URI)


def _error(status_code: int, code: str, message: str) -> Response:
    """Return an error as { code, message }, like lpdc-management-service."""

    logger.warning("Rewrite error %s: %s", code, message)
    return Response(
        json.dumps({"code": code, "message": message}, ensure_ascii=False),
        status=status_code,
        mimetype="application/json",
    )


@bp.app_errorhandler(HTTPException)
def _http_error(error: HTTPException) -> Response:
    """Return Flask routing errors such as 404 and 405 as { code, message }."""

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
def create_rewrite() -> Response:
    """Create a task and return its URI while the rewrite runs in the background."""

    task_uris: tuple[str, str] | None = None

    def fail_task(error: Exception) -> None:
        if task_uris is not None:
            try:
                store.fail_rewrite_task(*task_uris, error)
            except Exception:
                logger.exception("Failed to persist failure for rewrite task %s", task_uris[0])

    try:
        store = get_store()
        config = get_config()
        agent_uri = get_agent()

        try:
            payload = RewriteRequest.model_validate(request.get_json(silent=True))
        except (ValueError, ValidationError):
            logger.debug("Invalid rewrite request")
            return _error(400, "invalid_request", "Request body is invalid")

        original_text = payload.original_text
        related_subject = payload.related_subject
        related_field = payload.related_field

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
            return _error(
                413,
                "input_too_large",
                "De tekst is langer dan de maximale lengte",
            )

        # Both forms resolve through authorization, which is the only access check.
        public_service_id = payload.public_service_id
        parsed_public_service_id = urlsplit(public_service_id)
        if parsed_public_service_id.scheme in {"http", "https"} and parsed_public_service_id.netloc:
            public_service = store.resolve_by_uri(public_service_id, INSTANCE_PUBLIC_SERVICE)
        else:
            public_service = store.resolve_by_uuid(public_service_id, INSTANCE_PUBLIC_SERVICE)
        if public_service is None:
            return _error(404, "public_service_not_found", "Public service not found")

        guideline_text = config.rewrite.guidelines
        field_generation_uri = store.find_or_create_field_generation(
            related_subject, related_field, public_service
        )

        address_form = store.read_language_variant(public_service) or "nl-be-x-formal"
        attempt_number = store.next_attempt_number(field_generation_uri)
        if attempt_number > config.limits.max_attempts:
            return _error(
                409, "attempt_limit_reached", "Het maximum aantal pogingen voor dit veld is bereikt"
            )

        # The prompt gets readable labels; provenance keeps the raw URI and variant.
        subject_types = store.read_subject_types(related_subject)
        prompt_field = field_label(config, subject_types, related_field)
        prompt_address_form = address_form_label(config, address_form)

        activity_uuid = str(uuid.uuid4())
        activity_uri = f"{DATA_URI_BASE}generation-activities/{activity_uuid}"
        job_uri = f"{JOB_URI_PREFIX}{uuid.uuid4()}"
        store.create_task(
            activity_uri=activity_uri,
            job_uri=job_uri,
            operation=TASK_OPERATION["REWRITE"],
            agent_uri=agent_uri,
            field_generation_uri=field_generation_uri,
            attempt_number=attempt_number,
            original_text=original_text,
        )
        task_uris = (activity_uri, job_uri)

        @copy_current_request_context
        def run_rewrite() -> None:
            try:
                started_at = datetime.now(timezone.utc)
                store.start_rewrite_task(activity_uri, job_uri, started_at)
                result, usage = asyncio.run(
                    rewrite_text(
                        config,
                        get_llm(),
                        field=prompt_field,
                        address_form=prompt_address_form,
                        guideline_text=guideline_text,
                        text=original_text,
                    )
                )
                ended_at = datetime.now(timezone.utc)

                store.write_generation_activity(
                    activity_uri=activity_uri,
                    job_uri=job_uri,
                    generated_text=result.generated_text,
                    started_at=started_at,
                    ended_at=ended_at,
                    address_form=address_form,
                    agent_uri=agent_uri,
                    model_uri=model_name_to_uri(config.llm.model_name),
                    endpoint=config.llm.base_url or config.llm.provider,
                    tokens_in=usage.tokens_in,
                    tokens_out=usage.tokens_out,
                    duration=usage.duration,
                )
            except Exception as error:
                logger.exception("Rewrite task %s failed", activity_uri)
                fail_task(error)

        Thread(target=run_rewrite).start()
        return Response(
            json.dumps(
                {
                    "status": JOB_STATUS["SCHEDULED"],
                    "id": activity_uuid,
                    "generation_activity_uri": activity_uri,
                }
            ),
            status=202,
            mimetype="application/json",
        )
    except Exception as error:
        logger.exception("Rewrite request failed")
        fail_task(error)
        return _error(500, "rewrite_failed", "The rewrite request failed")


# The template publishes its Flask app on builtins before importing this
# module. Without it (e.g. unit tests mounting bp on their own app) there is
# nothing to register on and no triplestore to wait for.
if hasattr(builtins, "app"):
    builtins.app.register_blueprint(bp)
    _startup()
