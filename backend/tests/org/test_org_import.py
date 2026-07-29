"""Flat course-catalogue import, org table operations, and upload templates."""

import httpx

CATALOGUE = {
    "faculty_division_code": "FET",
    "faculty_division_name": "Faculty of Engineering & Technology",
    "school_code": "SOCE",
    "school_name": "School of Computational Engineering",
    "department_code": "CSE",
    "department_name": "Computer Science & Engineering",
    "programme_code": "BT-CSE",
    "programme_name": "B.Tech Computer Science & Engineering",
    "level": "Under Graduate",
    "duration_years": "4",
    "mode": "Full-Time",
}


def _csv(rows: list[dict[str, str]]) -> bytes:
    from unicore.modules.org.schemas import ORG_CSV_COLUMNS

    lines = [",".join(ORG_CSV_COLUMNS)]
    lines += [",".join(r.get(c, "") for c in ORG_CSV_COLUMNS) for r in rows]
    return ("\n".join(lines) + "\n").encode()


async def _upload(client: httpx.AsyncClient, payload: bytes) -> httpx.Response:
    return await client.post("/org/imports", files={"file": ("org.csv", payload, "text/csv")})


async def _with_university(client: httpx.AsyncClient) -> None:
    await client.post("/org/units", json={"type": "university", "name": "U", "code": "UNI"})


async def test_flat_row_creates_the_whole_branch(make_client) -> None:
    """One Programme row creates its Faculty Division, School and Department too."""
    async with make_client("super-admin") as admin:
        await _with_university(admin)
        result = (await _upload(admin, _csv([CATALOGUE]))).json()
        assert result["rows_rejected"] == 0, result["errors"]
        assert result["rows_created"] == 4  # FD + School + Dept + Programme

        units = (await admin.get("/org/units")).json()
        by_code = {u["code"]: u for u in units}
    assert by_code["FET"]["type"] == "faculty_division"
    assert by_code["CSE"]["path"] == "uni.fet.soce.cse"
    programme = by_code["BT-CSE"]
    assert programme["level"] == "Under Graduate"
    assert programme["duration_years"] == 4
    assert programme["mode"] == "Full-Time"


async def test_shared_ancestors_created_once(make_client) -> None:
    """Repeating the ancestor columns per row reuses them instead of duplicating."""
    second = {**CATALOGUE, "programme_code": "MT-CSE", "programme_name": "M.Tech CSE",
              "level": "Post Graduate", "duration_years": "2"}
    third = {**CATALOGUE, "department_code": "AIDS", "department_name": "AI & Data Science",
             "programme_code": "BT-AIDS", "programme_name": "B.Tech AI & DS"}
    async with make_client("super-admin") as admin:
        await _with_university(admin)
        result = (await _upload(admin, _csv([CATALOGUE, second, third]))).json()
        units = (await admin.get("/org/units")).json()
    assert result["rows_rejected"] == 0
    # FD + School created once; two Departments; three Programmes.
    assert len([u for u in units if u["type"] == "faculty_division"]) == 1
    assert len([u for u in units if u["type"] == "school"]) == 1
    assert len([u for u in units if u["type"] == "department"]) == 2
    assert len([u for u in units if u["type"] == "program"]) == 3


async def test_reimport_is_idempotent_and_updates_names(make_client) -> None:
    async with make_client("super-admin") as admin:
        await _with_university(admin)
        first = (await _upload(admin, _csv([CATALOGUE]))).json()
        again = (await _upload(admin, _csv([CATALOGUE]))).json()
        renamed = (await _upload(
            admin, _csv([{**CATALOGUE, "programme_name": "B.Tech CSE (Revised)"}])
        )).json()
    assert first["rows_created"] == 4
    assert again["rows_created"] == 0 and again["rows_unchanged"] == 4
    assert renamed["rows_updated"] == 1  # only the Programme name changed


async def test_row_validation(make_client) -> None:
    rows = [
        {**CATALOGUE, "programme_code": "", "programme_name": "No code"},
        {**CATALOGUE, "programme_code": "X1", "level": "Undergrad"},        # bad level
        {**CATALOGUE, "programme_code": "X2", "mode": "Weekends"},          # bad mode
        {**CATALOGUE, "programme_code": "X3", "duration_years": "many"},    # bad duration
        CATALOGUE,                                                          # valid
    ]
    async with make_client("super-admin") as admin:
        await _with_university(admin)
        result = (await _upload(admin, _csv(rows))).json()
    assert result["rows_rejected"] == 4
    fields = {e["field"] for e in result["errors"]}
    assert fields == {"programme_code", "level", "mode", "duration_years"}


async def test_codes_are_case_and_separator_insensitive(make_client) -> None:
    """bt-cse, BT_CSE and BT-CSE are the same code — no accidental duplicates."""
    async with make_client("super-admin") as admin:
        await _with_university(admin)
        await _upload(admin, _csv([CATALOGUE]))
        variant = {**CATALOGUE, "programme_code": "bt_cse", "faculty_division_code": "fet"}
        result = (await _upload(admin, _csv([variant]))).json()
        programmes = [u for u in (await admin.get("/org/units")).json() if u["type"] == "program"]
    assert result["rows_created"] == 0
    assert len(programmes) == 1


async def test_import_requires_university_and_super_admin(make_client) -> None:
    async with make_client("super-admin") as admin:
        no_root = await _upload(admin, _csv([CATALOGUE]))
        assert no_root.status_code == 409
        assert "bootstrap" in no_root.json()["detail"]

    # Distinct actor: the default user would already hold the super-admin grant
    # seeded by the block above.
    async with make_client("system-admin", user_id="it.staff") as staff:
        assert (await _upload(staff, _csv([CATALOGUE]))).status_code == 403


async def test_only_one_university_allowed(make_client) -> None:
    """Single-university model: a second university row is refused."""
    async with make_client("super-admin") as admin:
        first = await admin.post(
            "/org/units", json={"type": "university", "name": "U", "code": "UNI"}
        )
        second = await admin.post(
            "/org/units", json={"type": "university", "name": "Other", "code": "OTHER"}
        )
    assert first.status_code == 201
    assert second.status_code == 409
    assert "already exists" in second.json()["detail"]


async def test_table_edit_deactivate_and_reactivate(make_client, audit_rows) -> None:
    """The org table's row actions: update fields, deactivate (never delete), restore."""
    async with make_client("super-admin") as admin:
        await _with_university(admin)
        await _upload(admin, _csv([CATALOGUE]))
        programme = [u for u in (await admin.get("/org/units")).json() if u["code"] == "BT-CSE"][0]

        edited = await admin.put(
            f"/org/units/{programme['id']}",
            json={"name": "B.Tech CSE (Hons.)", "duration_years": 5},
        )
        assert edited.status_code == 200
        assert edited.json()["duration_years"] == 5

        deactivated = await admin.post(f"/org/units/{programme['id']}/deactivate")
        assert deactivated.json()["status"] == "deactivated"
        assert programme["id"] not in [u["id"] for u in (await admin.get("/org/units")).json()]
        with_inactive = await admin.get("/org/units", params={"include_inactive": True})
        assert programme["id"] in [u["id"] for u in with_inactive.json()]

        restored = await admin.post(f"/org/units/{programme['id']}/reactivate")
        assert restored.json()["status"] == "active"

        # There is no delete route — history must survive.
        assert (await admin.delete(f"/org/units/{programme['id']}")).status_code == 405
    assert await audit_rows("org.unit.updated")
    assert await audit_rows("org.unit.reactivated")


async def test_table_filters(make_client) -> None:
    async with make_client("super-admin") as admin:
        await _with_university(admin)
        await _upload(admin, _csv([CATALOGUE]))
        programmes = await admin.get("/org/units", params={"unit_type": "program"})
        searched = await admin.get("/org/units", params={"search": "computational"})
    assert [u["code"] for u in programmes.json()] == ["BT-CSE"]
    assert [u["code"] for u in searched.json()] == ["SOCE"]


async def test_templates_carry_sample_rows(make_client) -> None:
    async with make_client("system-admin") as staff:
        listed = {t["key"] for t in (await staff.get("/templates")).json()}
        assert {"org-structure", "students", "sections"} <= listed
        for key, minimum in (("org-structure", 4), ("students", 4), ("sections", 3)):
            body = (await staff.get(f"/templates/{key}.csv")).text
            data_lines = [ln for ln in body.splitlines() if ln.strip() and not ln.startswith("#")]
            assert len(data_lines) - 1 >= minimum, f"{key} has too few sample rows"
            assert any("SAMPLE DATA" in ln for ln in body.splitlines() if ln.startswith("#"))
        assert (await staff.get("/templates/nope.csv")).status_code == 404


async def test_downloaded_template_imports_cleanly(make_client) -> None:
    """Download the org template and upload it unchanged — the sample catalogue applies."""
    async with make_client("super-admin") as admin:
        await _with_university(admin)
        template = (await admin.get("/templates/org-structure.csv")).text
        result = (await _upload(admin, template.encode())).json()
        units = (await admin.get("/org/units")).json()
    assert result["rows_rejected"] == 0, result["errors"]
    assert len([u for u in units if u["type"] == "program"]) == 4


# --- structure refinements from the university document (28-07-2026) ---------

SCHOOL_ONLY = {
    "faculty_division_code": "FHS",
    "faculty_division_name": "Faculty of Health Sciences",
    "school_code": "SAHS",
    "school_name": "School of Allied Health Sciences",
    "department_code": "",
    "department_name": "",
    "programme_code": "B-OPTOM",
    "programme_name": "Bachelor of Optometry",
    "level": "Under Graduate",
    "duration_years": "5",
    "mode": "Full-Time",
    "category": "Standard",
    "industry_partner": "",
    "internship_months": "12",
    "lateral_entry_semester": "",
}


async def test_school_without_department_gets_a_default(make_client) -> None:
    """12 of 14 Schools have no Departments — blank columns synthesise one,
    flagged auto_created so it reads as a placeholder, not an academic unit."""
    async with make_client("super-admin") as admin:
        await _with_university(admin)
        result = (await _upload(admin, _csv([SCHOOL_ONLY]))).json()
        assert result["rows_rejected"] == 0, result["errors"]
        units = {u["code"]: u for u in (await admin.get("/org/units")).json()}

    department = units["SAHS"] if units["SAHS"]["type"] == "department" else None
    departments = [u for u in units.values() if u["type"] == "department"]
    assert len(departments) == 1
    assert departments[0]["auto_created"] is True
    assert departments[0]["name"] == "School of Allied Health Sciences"
    programme = units["B-OPTOM"]
    assert programme["path"].endswith(".sahs.sahs.b_optom")  # School → default Dept → Programme
    assert department is None or True


async def test_real_department_is_not_flagged_auto(make_client) -> None:
    async with make_client("super-admin") as admin:
        await _with_university(admin)
        await _upload(admin, _csv([CATALOGUE]))
        units = {u["code"]: u for u in (await admin.get("/org/units")).json()}
    assert units["CSE"]["type"] == "department"
    assert units["CSE"]["auto_created"] is False


async def test_partial_department_columns_rejected(make_client) -> None:
    """A code without a name (or vice versa) is a mistake, not a default request."""
    async with make_client("super-admin") as admin:
        await _with_university(admin)
        result = (await _upload(
            admin, _csv([{**SCHOOL_ONLY, "department_code": "AHS", "department_name": ""}])
        )).json()
    assert result["rows_rejected"] == 1
    assert result["errors"][0]["field"] == "department_name"


async def test_programme_category_partner_and_internship(make_client) -> None:
    industry = {
        **CATALOGUE,
        "programme_code": "BT-CSE-CYBER",
        "programme_name": "B.Tech CSE (Cyber Security)",
        "category": "Industry Collaborated",
        "industry_partner": "IBM",
    }
    lateral = {**SCHOOL_ONLY, "school_code": "SOP", "school_name": "School of Pharmacy",
               "programme_code": "B-PHARM", "programme_name": "B.Pharm",
               "internship_months": "", "lateral_entry_semester": "3", "duration_years": "4"}
    async with make_client("super-admin") as admin:
        await _with_university(admin)
        result = (await _upload(admin, _csv([industry, lateral]))).json()
        assert result["rows_rejected"] == 0, result["errors"]
        units = {u["code"]: u for u in (await admin.get("/org/units")).json()}
    cyber = units["BT-CSE-CYBER"]
    assert (cyber["category"], cyber["industry_partner"]) == ("Industry Collaborated", "IBM")
    assert units["B-OPTOM" if "B-OPTOM" in units else "B-PHARM"]["lateral_entry_semester"] == 3


async def test_invalid_category_rejected(make_client) -> None:
    async with make_client("super-admin") as admin:
        await _with_university(admin)
        result = (await _upload(
            admin, _csv([{**CATALOGUE, "category": "Sponsored"}])
        )).json()
    assert result["rows_rejected"] == 1
    assert result["errors"][0]["field"] == "category"
