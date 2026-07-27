"""HTTP endpoints for the audit module. No business logic here (see ARCHITECTURE.md)."""

from fastapi import APIRouter

router = APIRouter(prefix="/audit", tags=["audit"])
