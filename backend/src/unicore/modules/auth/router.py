"""HTTP endpoints for the auth module. No business logic here (see ARCHITECTURE.md)."""

from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])
