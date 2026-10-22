"""JSON:API request and response models for HH rewrite attempts.

Python field names use snake_case, while aliases preserve the hyphenated
JSON:API attribute and relationship names used by the public contract.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ResourceIdentifier(BaseModel):
    """JSON:API identifier used inside a relationship."""

    type: str
    id: str


class Relationship(BaseModel):
    """Optional JSON:API relationship data."""

    data: ResourceIdentifier | None = None


class RewriteAttributes(BaseModel):
    """Input attributes required to request a rewrite."""

    model_config = ConfigDict(populate_by_name=True)

    original_text: str = Field(alias="original-text")
    related_subject: str = Field(alias="related-subject")
    related_field: str = Field(alias="related-field")


class RewriteRelationships(BaseModel):
    """Relationships accepted by the rewrite request."""

    model_config = ConfigDict(populate_by_name=True)

    public_service: Relationship = Field(
        default_factory=Relationship, alias="public-service"
    )
    field_generation: Relationship = Field(
        default_factory=Relationship, alias="field-generation"
    )
    based_on: Relationship = Field(default_factory=Relationship, alias="based-on")
    guideline: Relationship = Field(default_factory=Relationship, alias="guideline")


class RewriteRequestData(BaseModel):
    """JSON:API request resource containing rewrite data."""

    model_config = ConfigDict(populate_by_name=True)

    type: str
    attributes: RewriteAttributes
    relationships: RewriteRelationships = Field(default_factory=RewriteRelationships)


class RewriteRequest(BaseModel):
    """Top-level JSON:API rewrite request document."""

    data: RewriteRequestData


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ActivityAttributes(BaseModel):
    """Provenance activity attributes returned with a completed attempt."""

    model_config = ConfigDict(populate_by_name=True)

    uri: str
    started_at: str = Field(alias="started-at")
    ended_at: str = Field(alias="ended-at")
    address_form: str = Field(alias="address-form")
    language: str
    status: str


class AttemptAttributes(BaseModel):
    """Generated-attempt attributes returned to the caller."""

    model_config = ConfigDict(populate_by_name=True)

    uri: str
    original_text: str = Field(alias="original-text")
    generated_text: str = Field(alias="generated-text")
    attempt_number: int = Field(alias="attempt-number")
    status: str


class Resource(BaseModel):
    """Generic JSON:API resource used for primary and included data."""

    type: str
    id: str
    attributes: dict[str, Any]
    relationships: dict[str, Any]


class RewriteResponse(BaseModel):
    """Top-level response containing an attempt and its activity."""

    data: Resource
    included: list[Resource]