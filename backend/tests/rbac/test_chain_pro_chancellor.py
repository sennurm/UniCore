"""Pro-Chancellor restored per the university structure document (28-07-2026):
VC/Registrar -> Pro-Chancellor -> Chancellor, and NOT a singleton."""

import httpx


async def _user(admin: httpx.AsyncClient, username: str) -> dict:
    response = await admin.post(
        "/user", json={"username": username, "full_name": username, "kind": "staff"}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_two_pro_chancellors_allowed(make_client) -> None:
    """The leadership page lists two holders — the singleton rule must not apply."""
    async with make_client("super-admin") as admin:
        first = await _user(admin, "pc.one")
        second = await _user(admin, "pc.two")
        a = await admin.post(
            "/rbac/grants", json={"user_id": first["id"], "role_code": "pro-chancellor"}
        )
        b = await admin.post(
            "/rbac/grants", json={"user_id": second["id"], "role_code": "pro-chancellor"}
        )
    assert a.status_code == 201
    assert b.status_code == 201  # would be 409 if singleton-enforced


async def test_vc_and_registrar_report_to_pro_chancellor(make_client) -> None:
    async with make_client("super-admin") as admin:
        pc = await _user(admin, "pc.holder")
        vc = await _user(admin, "vc.holder")
        registrar = await _user(admin, "registrar.holder")
        for user, role in ((pc, "pro-chancellor"), (vc, "vc"), (registrar, "registrar")):
            issued = await admin.post(
                "/rbac/grants", json={"user_id": user["id"], "role_code": role}
            )
            assert issued.status_code == 201, issued.text

        vc_chain = (await admin.get(f"/rbac/users/{vc['id']}/reporting")).json()
        reg_chain = (await admin.get(f"/rbac/users/{registrar['id']}/reporting")).json()
        pc_chain = (await admin.get(f"/rbac/users/{pc['id']}/reporting")).json()
    assert vc_chain[0]["reports_to"] == "pro-chancellor"
    assert vc_chain[0]["holders"] == [pc["id"]]
    assert reg_chain[0]["reports_to"] == "pro-chancellor"
    assert pc_chain[0]["reports_to"] == "chancellor"
    assert pc_chain[0]["status"] == "vacant"  # no Chancellor named in the document
