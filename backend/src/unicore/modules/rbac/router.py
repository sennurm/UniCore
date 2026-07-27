"""HTTP endpoints for the rbac module. No business logic here (see ARCHITECTURE.md)."""

from fastapi import APIRouter

router = APIRouter(prefix="/rbac", tags=["rbac"])
