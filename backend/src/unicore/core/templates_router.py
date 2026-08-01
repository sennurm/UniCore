"""Template listing + download.

Templates are blank schemas, never data, so these once shipped authenticated but
without a role check. The project rule admits no exception — deny by default,
role check on every endpoint — so they now gate on `templates:read` via the
registered checker, which keeps core/ free of any import from modules/.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from unicore.core import templates
from unicore.core.security import AuthContext, requires

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("")
async def list_templates(
    ctx: AuthContext = Depends(requires("templates:read")),
) -> list[dict[str, object]]:
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
async def download_template(
    key: str, ctx: AuthContext = Depends(requires("templates:read"))
) -> PlainTextResponse:
    template = templates.get(key)
    if template is None:
        raise HTTPException(status_code=404, detail=f"No template '{key}'.")
    return PlainTextResponse(
        template.render(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{template.filename}"'},
    )
