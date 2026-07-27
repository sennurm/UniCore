"""Pydantic request/response models for the rbac module."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class GrantCreate(BaseModel):
    user_id: uuid.UUID
    role_code: str
    org_unit_id: uuid.UUID | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    term_code: str | None = None
    additional_charge: bool = False


class GrantRevoke(BaseModel):
    reason: str


class SupersedeRequest(BaseModel):
    role_code: str
    org_unit_id: uuid.UUID | None = None
    new_user_id: uuid.UUID
    term_code: str | None = None


class GrantOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    role_code: str
    org_unit_id: uuid.UUID | None
    status: str
    valid_from: datetime | None
    valid_until: datetime | None
    term_code: str | None
    additional_charge: bool
    revoke_cause: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
