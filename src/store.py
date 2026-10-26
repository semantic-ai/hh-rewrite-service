"""SPARQL persistence for field generations, attempts and provenance.

The mu-python-template supplies query, update and escaping functions. This
module owns only LPDC domain queries and the single persistence operation that
writes an immutable generation attempt with its provenance records.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from escape_helpers import sparql_escape, sparql_escape_float, sparql_escape_int, sparql_escape_uri
from helpers import query as template_query, update as template_update


# ---------------------------------------------------------------------------
# Vocabulary constants
# ---------------------------------------------------------------------------


LPDC = "https://productencatalogus.data.vlaanderen.be/ns/ipdc-lpdc#"
MU = "http://mu.semte.ch/vocabularies/core/"
PROV = "http://www.w3.org/ns/prov#"
EXT = "http://mu.semte.ch/vocabularies/ext/"
DCT = "http://purl.org/dc/terms/"


# ---------------------------------------------------------------------------
# SPARQL input and result helpers
# ---------------------------------------------------------------------------


def _uuid_uri(prefix: str) -> str:
    """Create a new data URI for a generated LPDC resource."""

    return f"http://data.lblod.info/id/{prefix}/{uuid.uuid4()}"


def _value(result: dict, key: str) -> str | None:
    bindings = result.get("results", {}).get("bindings", [])
    if not bindings:
        return None
    value = bindings[0].get(key)
    return value.get("value") if value else None


# ---------------------------------------------------------------------------
# Domain store
# ---------------------------------------------------------------------------


class Store:
    """Persist LPDC field generations, rewrite attempts and provenance."""

    def read_guideline(self, guideline_uri: str) -> str | None:
        """Read the body of one typed guideline resource."""

        query = f"""SELECT ?text WHERE {{
            {sparql_escape_uri(guideline_uri)} a <{EXT}Guideline> ;
                <{MU}uuid> ?uuid ;
                <{DCT}title> ?title ;
                <{EXT}guidelineText> ?text .
        }} LIMIT 1"""
        return _value(template_query(query), "text")

    def read_language_variant(self, public_service_uri: str) -> str | None:
        """Read the Dutch address form configured for a public service."""

        query = f"""SELECT ?variant WHERE {{
            {sparql_escape_uri(public_service_uri)} <{LPDC}dutchLanguageVariant> ?variant
        }} LIMIT 1"""
        return _value(template_query(query), "variant")

    def resolve_by_uuid(self, identifier: str, rdf_type: str) -> str | None:
        """Resolve a typed JSON:API identifier through authorization."""

        query = f"""SELECT ?resource WHERE {{
            ?resource a {sparql_escape_uri(rdf_type)} ;
                <{MU}uuid> {sparql_escape(identifier)} .
        }} LIMIT 1"""
        return _value(template_query(query), "resource")

    def find_or_create_field_generation(
        self, related_subject: str, related_field: str, public_service: str
    ) -> str:
        """Find a field generation or create its domain resource when absent."""

        query = f"""SELECT ?generation WHERE {{
            ?generation a <{LPDC}FieldGeneration> ;
            <{LPDC}relatedSubject> {sparql_escape_uri(related_subject)} ;
            <{LPDC}relatedField> {sparql_escape(related_field)} ;
            <{LPDC}fieldGenerationInstance> {sparql_escape_uri(public_service)} .
        }} LIMIT 1"""
        existing = _value(template_query(query), "generation")
        if existing:
            return existing
        generation = _uuid_uri("field-generations")
        triples = "\n".join(
            [
                f"{sparql_escape_uri(generation)} a <{LPDC}FieldGeneration> ;",
                f"    <{MU}uuid> {sparql_escape(generation.rsplit('/', 1)[-1])} ;",
                f"    <{LPDC}relatedSubject> {sparql_escape_uri(related_subject)} ;",
                f"    <{LPDC}relatedField> {sparql_escape(related_field)} ;",
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
            <{LPDC}relatedField> {sparql_escape(related_field)} ;
            <{LPDC}fieldGenerationInstance> {sparql_escape_uri(public_service)} .
    }}
}}"""
        )
        existing = _value(template_query(query), "generation")
        return existing or generation

    def next_attempt_number(self, field_generation_uri: str) -> int:
        """Return the next sequential attempt number for a field generation."""

        query = f"""SELECT (MAX(?number) AS ?max) WHERE {{
            ?attempt <{LPDC}generationAttempt> {sparql_escape_uri(field_generation_uri)} ;
                <{LPDC}attemptNumber> ?number .
        }}"""
        maximum = _value(template_query(query), "max")
        try:
            return int(maximum) + 1 if maximum is not None else 1
        except (TypeError, ValueError):
            return 1

    def write_attempt(
        self,
        *,
        attempt_uri: str,
        field_generation_uri: str,
        attempt_number: int,
        original_text: str,
        generated_text: str,
        activity_uri: str,
        started_at: datetime,
        ended_at: datetime,
        address_form: str,
        guideline_uri: str,
        agent_uri: str,
        model_uri: str | None = None,
        endpoint: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        duration: float | None = None,
    ) -> None:
        """Write one attempt and its provenance records in a single update."""

        # Domain data identifies the immutable generated attempt.
        attempt_lines = [
            f"{sparql_escape_uri(attempt_uri)} a <{LPDC}GenerationAttempt> ;",
            f"    <{MU}uuid> {sparql_escape(attempt_uri.rsplit('/', 1)[-1])} ;",
            f"    <{LPDC}generationAttempt> {sparql_escape_uri(field_generation_uri)} ;",
            f"    <{LPDC}attemptNumber> {sparql_escape(attempt_number)} ;",
            f"    <{LPDC}originalText> {sparql_escape(original_text)} ;",
            f"    <{LPDC}generatedText> {sparql_escape(generated_text)} ;",
            f"    <{PROV}wasGeneratedBy> {sparql_escape_uri(activity_uri)} .",
        ]

        # Provenance records capture timing, guideline and agent metadata.
        activity_lines = [
            f"{sparql_escape_uri(activity_uri)} a <{PROV}Activity> ;",
            f"    <{MU}uuid> {sparql_escape(activity_uri.rsplit('/', 1)[-1])} ;",
            f"    <{PROV}startedAtTime> {sparql_escape(started_at.astimezone(timezone.utc))} ;",
            f"    <{PROV}endedAtTime> {sparql_escape(ended_at.astimezone(timezone.utc))} ;",
            f"    <{PROV}generated> {sparql_escape_uri(attempt_uri)} ;",
            f"    <{PROV}wasAssociatedWith> {sparql_escape_uri(agent_uri)} ;",
            f"    <{PROV}used> {sparql_escape_uri(guideline_uri)} ;",
            f"    <{EXT}addressForm> {sparql_escape(address_form)} ;",
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

        domain = "\n".join(attempt_lines)
        provenance = "\n".join(activity_lines)
        if ai_call_lines:
            provenance += "\n" + "\n".join(ai_call_lines)
        template_update("INSERT DATA {\n" + domain + "\n" + provenance + "\n}")