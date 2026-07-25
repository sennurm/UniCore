from fastapi import APIRouter

from unicore import __version__

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe: no external dependencies touched."""
    return {"status": "ok", "version": __version__}
