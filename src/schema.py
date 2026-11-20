"""Request model for HH rewrites.

Python field names use snake_case, while aliases match the camelCase JSON body
sent by the frontend, like the lpdc-management-service endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RewriteRequest(BaseModel):
    """Plain JSON body of a rewrite request."""

    model_config = ConfigDict(populate_by_name=True)

    public_service_id: str = Field(alias="publicServiceId")
    related_subject: str = Field(alias="relatedSubject")
    related_field: str = Field(alias="relatedField")
    original_text: str = Field(alias="originalText")
