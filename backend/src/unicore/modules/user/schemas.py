"""Pydantic request/response models for the user module."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100, pattern=r"^[a-z0-9._-]+$")
    full_name: str = Field(min_length=1, max_length=200)
    kind: str = Field(pattern=r"^(student|staff)$")
    erp_id: str | None = Field(default=None, max_length=100)
    email: str | None = None
    mobile: str | None = None


class UserOut(BaseModel):
    """Never exposes credential fields (password_hash) — audience-appropriate only."""

    id: uuid.UUID
    username: str
    erp_id: str | None
    full_name: str
    email: str | None
    mobile: str | None
    kind: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
