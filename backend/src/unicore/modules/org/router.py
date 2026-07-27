"""HTTP endpoints for the org module. No business logic here (see ARCHITECTURE.md)."""

from fastapi import APIRouter

router = APIRouter(prefix="/org", tags=["org"])
