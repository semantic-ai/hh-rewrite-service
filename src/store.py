"""SPARQL persistence for field generations, activities and provenance.

The mu-python-template supplies query, update and escaping functions. This
module owns LPDC domain queries, job/task state and generation provenance.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from escape_helpers import sparql_escape, sparql_escape_float, sparql_escape_int, sparql_escape_uri
from helpers import query as template_query, update as template_update

from .constants import (
    ADMS,
    DATA_URI_BASE,
    DCT,
    ERROR_URI_PREFIX,
    EXT,
    GENERATION_ACTIVITY,
    HAS_GENERATION_ACTIVITY,
    JOB_OPERATION,
    JOB_STATUS,
    JOB_TYPE,
    LPDC,
    LPDCEXT,
    MU,
    NOT_USED,
    OSLC,
    PROV,
    TASK,
    TASK_TYPE,
)


# ---------------------------------------------------------------------------
# SPARQL input and result helpers
# ---------------------------------------------------------------------------


def _uuid_uri(prefix: str) -> str:
    """Create a new data URI for a generated LPDC resource."""

    return f"{DATA_URI_BASE}{prefix}/{uuid.uuid4()}"


def _value(result: dict, key: str) -> str | None:
    bindings = result.get("results", {}).get("bindings", [])
    if not bindings:
        return None
    value = bindings[0].get(key)
    return value.get("value") if value else None


def _datetime(value: datetime) -> str:
    return (
        f"{sparql_escape(value.astimezone(timezone.utc).isoformat())}"
        "^^<http://www.w3.org/2001/XMLSchema#dateTime>"
    )


# ---------------------------------------------------------------------------
# Domain store
# ---------------------------------------------------------------------------


class Store:
    """Persist LPDC field generations, rewrite attempts and provenance."""

    def read_language_variant(self, public_service_uri: str) -> str | None:
        """Read the Dutch address form configured for a public service."""

        query = f"""SELECT ?variant WHERE {{
            {sparql_escape_uri(public_service_uri)} <{LPDCEXT}dutchLanguageVariant> ?variant
        }} LIMIT 1"""
        return _value(template_query(query), "variant")

    def read_subject_types(self, subject_uri: str) -> list[str]:
        """Read the RDF types of the resource that owns the rewritten field."""

        query = f"""SELECT DISTINCT ?type WHERE {{
            {sparql_escape_uri(subject_uri)} a ?type
        }}"""
        bindings = template_query(query).get("results", {}).get("bindings", [])
        return [binding["type"]["value"] for binding in bindings if "type" in binding]

    def resolve_by_uuid(self, identifier: str, rdf_type: str) -> str | None:
        """Resolve a typed JSON:API identifier through authorization."""

        query = f"""SELECT ?resource WHERE {{
            ?resource a {sparql_escape_uri(rdf_type)} ;
                <{MU}uuid> {sparql_escape(identifier)} .
        }} LIMIT 1"""
        return _value(template_query(query), "resource")

    def resolve_by_uri(self, uri: str, rdf_type: str) -> str | None:
        """Return the URI when the typed resource is visible through authorization."""

        query = f"""SELECT ?resource WHERE {{
            VALUES ?resource {{ {sparql_escape_uri(uri)} }}
            ?resource a {sparql_escape_uri(rdf_type)} .
        }} LIMIT 1"""
        return _value(template_query(query), "resource")

    def find_or_create_field_generation(
        self, related_subject: str, related_field: str, public_service: str
    ) -> str:
        """Find a field generation or create its domain resource when absent."""

        query = f"""SELECT ?generation WHERE {{
            ?generation a <{LPDC}FieldGeneration> ;
            <{LPDC}relatedSubject> {sparql_escape_uri(related_subject)} ;
            <{LPDC}relatedField> {sparql_escape_uri(related_field)} ;
            <{LPDC}fieldGenerationInstance> {sparql_escape_uri(public_service)} .
        }} LIMIT 1"""
        existing = _value(template_query(query), "generation")
        if existing:
            return existing
        # mu-cl-resources mints field generations under /id/generations/.
        generation = _uuid_uri("generations")
        triples = "\n".join(
            [
                f"{sparql_escape_uri(generation)} a <{LPDC}FieldGeneration> ;",
                f"    <{MU}uuid> {sparql_escape(generation.rsplit('/', 1)[-1])} ;",
                f"    <{LPDC}relatedSubject> {sparql_escape_uri(related_subject)} ;",
                f"    <{LPDC}relatedField> {sparql_escape_uri(related_field)} ;",
                f"    <{LPDC}fieldGenerationInstance> {sparql_escape_uri(public_service)} .",
            ]
        )
        template_update(
            f"""INSERT {{
{triples}
}} WHERE {{
    FILTER NOT EXISTS {{
        ?existing a <{LPDC}FieldGeneration> ;
            <{LPDC}relatedSubject> {sparql_escape_uri(related_subject)} ;
            <{LPDC}relatedField> {sparql_escape_uri(related_field)} ;
            <{LPDC}fieldGenerationInstance> {sparql_escape_uri(public_service)} .
    }}
}}"""
        )
        existing = _value(template_query(query), "generation")
        return existing or generation

    def next_attempt_number(self, field_generation_uri: str) -> int:
        """Return the next sequential attempt number for a field generation."""

        query = f"""SELECT (MAX(?number) AS ?max) WHERE {{
            {sparql_escape_uri(field_generation_uri)} <{HAS_GENERATION_ACTIVITY}> ?activity .
            ?activity a <{PROV}Activity> ;
                <{DCT}type> <{GENERATION_ACTIVITY}> ;
                <{LPDC}attemptNumber> ?number .
        }}"""
        maximum = _value(template_query(query), "max")
        try:
            return int(maximum) + 1 if maximum is not None else 1
        except (TypeError, ValueError):
            return 1

    def create_task(
        self,
        activity_uri: str,
        job_uri: str,
        operation: str,
        agent_uri: str,
        field_generation_uri: str,
        attempt_number: int,
        original_text: str,
    ) -> None:
        """Create the scheduled job and its task/activity together."""

        now = _datetime(datetime.now(timezone.utc))
        template_update(f"""INSERT DATA {{
            {sparql_escape_uri(job_uri)} a <{JOB_TYPE}> ;
                <{MU}uuid> {sparql_escape(job_uri.rsplit('/', 1)[-1])} ;
                <{DCT}creator> {sparql_escape_uri(agent_uri)} ;
                <{DCT}created> {now} ;
                <{DCT}modified> {now} ;
                <{TASK}operation> <{JOB_OPERATION}> ;
                <{ADMS}status> <{JOB_STATUS["SCHEDULED"]}> .
            {sparql_escape_uri(activity_uri)} a <{PROV}Activity>, <{TASK_TYPE}> ;
                <{DCT}type> <{GENERATION_ACTIVITY}> ;
                <{MU}uuid> {sparql_escape(activity_uri.rsplit('/', 1)[-1])} ;
                <{DCT}created> {now} ;
                <{DCT}modified> {now} ;
                <{DCT}isPartOf> {sparql_escape_uri(job_uri)} ;
                <{TASK}operation> {sparql_escape_uri(operation)} ;
                <{TASK}index> {sparql_escape("0")} ;
                <{PROV}wasAssociatedWith> {sparql_escape_uri(agent_uri)} ;
                <{LPDC}attemptNumber> {sparql_escape_int(attempt_number)} ;
                <{LPDC}originalText> {sparql_escape(original_text)} ;
                <{ADMS}status> <{JOB_STATUS["SCHEDULED"]}> .
            {sparql_escape_uri(field_generation_uri)} <{HAS_GENERATION_ACTIVITY}> {sparql_escape_uri(activity_uri)} .
        }}""")

    def _update_execution_status(
        self,
        activity_uri: str,
        job_uri: str,
        status: str,
        modified_at: datetime,
        triples: str = "",
    ) -> None:
        template_update(f"""DELETE {{
            ?resource <{ADMS}status> ?status ;
                <{DCT}modified> ?modified .
        }} INSERT {{
            ?resource <{ADMS}status> {sparql_escape_uri(status)} ;
                <{DCT}modified> {_datetime(modified_at)} .
            {triples}
        }} WHERE {{
            {sparql_escape_uri(activity_uri)} a <{TASK_TYPE}> ;
                <{ADMS}status> ?taskStatus ;
                <{DCT}modified> ?taskModified .
            {sparql_escape_uri(job_uri)} a <{JOB_TYPE}> ;
                <{ADMS}status> ?jobStatus ;
                <{DCT}modified> ?jobModified .
            VALUES ?resource {{ {sparql_escape_uri(activity_uri)} {sparql_escape_uri(job_uri)} }}
            ?resource <{ADMS}status> ?status ;
                <{DCT}modified> ?modified .
        }}""")
        result = template_query(f"""SELECT DISTINCT ?resource WHERE {{
            VALUES ?resource {{ {sparql_escape_uri(activity_uri)} {sparql_escape_uri(job_uri)} }}
            ?resource <{ADMS}status> {sparql_escape_uri(status)} .
        }}""")
        resources = {
            binding["resource"]["value"]
            for binding in result.get("results", {}).get("bindings", [])
        }
        if resources != {activity_uri, job_uri}:
            raise RuntimeError(f"Could not update rewrite job and task to {status}")

    def start_rewrite_task(
        self, activity_uri: str, job_uri: str, started_at: datetime
    ) -> None:
        self._update_execution_status(
            activity_uri, job_uri, JOB_STATUS["BUSY"], started_at,
            f"{sparql_escape_uri(activity_uri)} <{PROV}startedAtTime> {_datetime(started_at)} .",
        )

    def fail_rewrite_task(self, activity_uri: str, job_uri: str, error: Exception) -> None:
        """Fail the task and job together, retaining a linked error resource."""

        ended_at = datetime.now(timezone.utc)
        error_uri = f"{ERROR_URI_PREFIX}{uuid.uuid4()}"
        triples = f"""
            {sparql_escape_uri(activity_uri)} <{TASK}error> {sparql_escape_uri(error_uri)} ;
                <{PROV}endedAtTime> {_datetime(ended_at)} .
            {sparql_escape_uri(job_uri)} <{TASK}error> {sparql_escape_uri(error_uri)} .
            {sparql_escape_uri(error_uri)} a <{OSLC}Error> ;
                <{MU}uuid> {sparql_escape(error_uri.rsplit('/', 1)[-1])} ;
                <{DCT}modified> {_datetime(ended_at)} ;
                <{DCT}references> {sparql_escape_uri(activity_uri)} ;
                <{OSLC}message> {sparql_escape(f"{type(error).__name__}: {error}")} .
        """
        self._update_execution_status(activity_uri, job_uri, JOB_STATUS["FAILED"], ended_at, triples)

    def write_generation_activity(
        self,
        *,
        activity_uri: str,
        job_uri: str,
        generated_text: str,
        started_at: datetime,
        ended_at: datetime,
        address_form: str,
        agent_uri: str,
        model_uri: str | None = None,
        endpoint: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        duration: float | None = None,
    ) -> None:
        """Write the result/provenance and complete the task and job atomically."""

        activity_lines = [
            f"{sparql_escape_uri(activity_uri)} a <{PROV}Activity> ;",
            f"    a <{TASK_TYPE}> ;",
            f"    <{DCT}type> <{GENERATION_ACTIVITY}> ;",
            f"    <{MU}uuid> {sparql_escape(activity_uri.rsplit('/', 1)[-1])} ;",
            f"    <{LPDC}generatedText> {sparql_escape(generated_text)} ;",
            f"    <{LPDC}usageStatus> <{NOT_USED}> ;",
            f"    <{PROV}startedAtTime> {_datetime(started_at)} ;",
            f"    <{PROV}endedAtTime> {_datetime(ended_at)} ;",
            f"    <{PROV}wasAssociatedWith> {sparql_escape_uri(agent_uri)} ;",
            f"    <{LPDCEXT}dutchLanguageVariant> {sparql_escape(address_form)} ;",
            f"    <{DCT}language> {sparql_escape('nl')} .",
        ]

        if model_uri:
            ai_call_uri = _uuid_uri("ai-call")
            ai_call_lines = [
                f"{sparql_escape_uri(activity_uri)} <{EXT}performedAICall> {sparql_escape_uri(ai_call_uri)} .",
                f"{sparql_escape_uri(ai_call_uri)} a <{EXT}AICall> ;",
                f"    <{MU}uuid> {sparql_escape(ai_call_uri.rsplit('/', 1)[-1])} ;",
                f"    <{EXT}endpoint> {sparql_escape(endpoint or '')} ;",
                f"    <{EXT}aiModel> {sparql_escape_uri(model_uri)} ;",
                f"    <{EXT}tokenIn> {sparql_escape_int(tokens_in)} ;",
                f"    <{EXT}tokenOut> {sparql_escape_int(tokens_out)}",
            ]
            if duration is not None:
                ai_call_lines[-1] += " ;"
                ai_call_lines.append(f"    <{EXT}duration> {sparql_escape_float(duration)}")
            ai_call_lines[-1] += " ."
        else:
            ai_call_lines = []

        provenance = "\n".join(activity_lines)
        if ai_call_lines:
            provenance += "\n" + "\n".join(ai_call_lines)
        self._update_execution_status(
            activity_uri, job_uri, JOB_STATUS["SUCCESS"], ended_at, provenance
        )
