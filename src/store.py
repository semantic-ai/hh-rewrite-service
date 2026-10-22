"""SPARQL persistence for field generations, attempts and provenance.

The mu-python-template supplies query, update and escaping functions. This
module owns only LPDC domain queries and the single persistence operation that
writes an immutable generation attempt with its provenance records.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from escape_helpers import sparql_escape, sparql_escape_uri
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

    def __init__(
        self,
        *,
        domain_graph: str,
        provenance_graph: str,
    ) -> None:
        """Configure the store for the named graphs."""

        self.domain_graph = domain_graph
        self.provenance_graph = provenance_graph

    def read_guideline(self, guideline_uri: str) -> str | None:
        """Read the body of one typed guideline resource from the domain graph."""

        query = f"""SELECT ?text WHERE {{ GRAPH {sparql_escape_uri(self.domain_graph)} {{
            {sparql_escape_uri(guideline_uri)} a <{EXT}Guideline> ;
                <{MU}uuid> ?uuid ;
                <{DCT}title> ?title ;
                <{EXT}guidelineText> ?text .
        }} }} LIMIT 1"""
        return _value(template_query(query), "text")

    def read_language_variant(self, public_service_uri: str) -> str | None:
        """Read the Dutch address form configured for a public service."""

        query = f"""SELECT ?variant WHERE {{ GRAPH {sparql_escape_uri(self.domain_graph)} {{
            {sparql_escape_uri(public_service_uri)} <{LPDC}dutchLanguageVariant> ?variant
        }} }} LIMIT 1"""
        return _value(template_query(query), "variant")

    def find_or_create_field_generation(
        self, related_subject: str, related_field: str, public_service: str
    ) -> str:
        """Find a field generation or create its domain resource when absent."""

        query = f"""SELECT ?generation WHERE {{ GRAPH {sparql_escape_uri(self.domain_graph)} {{
            ?generation a <{LPDC}FieldGeneration> ;
            <{LPDC}relatedSubject> {sparql_escape_uri(related_subject)} ;
            <{LPDC}relatedField> {sparql_escape(related_field)} ;
            <{LPDC}fieldGenerationInstance> {sparql_escape_uri(public_service)} .
        }} }} LIMIT 1"""
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
        template_update(f"INSERT DATA {{ GRAPH {sparql_escape_uri(self.domain_graph)} {{\n{triples}\n}} }}")
        return generation

    def next_attempt_number(self, field_generation_uri: str) -> int:
        """Return the next sequential attempt number for a field generation."""

        query = f"""SELECT (MAX(?number) AS ?max) WHERE {{ GRAPH {sparql_escape_uri(self.domain_graph)} {{
            ?attempt <{LPDC}generationAttempt> {sparql_escape_uri(field_generation_uri)} ;
                <{LPDC}attemptNumber> ?number .
        }} }}"""
        maximum = _value(template_query(query), "max")
        try:
            return int(maximum) + 1 if maximum is not None else 1
        except (TypeError, ValueError):
            return 1

    def load_attempt(self, attempt_uri: str) -> dict[str, str] | None:
        """Load a predecessor attempt and the generation it belongs to."""

        query = f"""SELECT ?generation ?generated WHERE {{ GRAPH {sparql_escape_uri(self.domain_graph)} {{
            {sparql_escape_uri(attempt_uri)} <{LPDC}generationAttempt> ?generation ;
                <{LPDC}generatedText> ?generated .
        }} }} LIMIT 1"""
        result = template_query(query)
        generation = _value(result, "generation")
        generated = _value(result, "generated")
        if not generation or generated is None:
            return None
        return {"uri": attempt_uri, "field_generation": generation, "generated_text": generated}

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
        based_on_uri: str | None = None,
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
        if based_on_uri:
            attempt_lines[-1] = attempt_lines[-1][:-2] + " ;"
            attempt_lines.append(f"    <{LPDC}basedOn> {sparql_escape_uri(based_on_uri)} .")

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
        if based_on_uri:
            activity_lines[-1] = activity_lines[-1][:-2] + " ;"
            activity_lines.append(f"    <{PROV}used> {sparql_escape_uri(based_on_uri)} .")

        domain = "\n".join(attempt_lines)
        provenance = "\n".join(activity_lines)
        blocks = [f"GRAPH {sparql_escape_uri(self.domain_graph)} {{\n{domain}\n}}"]
        # The service supports either one shared graph or separate domain and provenance graphs.
        if self.provenance_graph == self.domain_graph:
            blocks[0] = f"GRAPH {sparql_escape_uri(self.domain_graph)} {{\n{domain}\n{provenance}\n}}"
        else:
            blocks.append(f"GRAPH {sparql_escape_uri(self.provenance_graph)} {{\n{provenance}\n}}")
        template_update("INSERT DATA {\n" + "\n".join(blocks) + "\n}")