"""Template listing + download. Authenticated (project security rule) but not
role-restricted: templates are blank schemas, never data."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from unicore.core import templates

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("")
async def list_templates() -> list[dict[str, object]]:
    return [
        {
            "key": t.key,
            "title": t.title,
            "description": t.description,
            "columns": list(t.columns),
            "download_url": f"/templates/{t.key}.csv",
        }
        for t in templates.all_templates()
    ]


@router.get("/{key}.csv", response_class=PlainTextResponse)
async def download_template(key: str) -> PlainTextResponse:
    template = templates.get(key)
    if template is None:
        raise HTTPException(status_code=404, detail=f"No template '{key}'.")
    return PlainTextResponse(
        template.render(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{template.filename}"'},
    )
