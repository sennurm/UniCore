"""HTTP endpoints for the user module. No business logic here (see ARCHITECTURE.md)."""

from fastapi import APIRouter

router = APIRouter(prefix="/user", tags=["user"])
