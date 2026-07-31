"""Load the university's real org structure into an empty (or partial) database.

    python -m unicore.seed [--catalogue PATH] [--university-name ...] [--university-code ...]

Runs the *same* validation pipeline the Bulk-upload screen uses, so a seeded
database is indistinguishable from one an administrator loaded by hand. Safe to
re-run: unchanged units are left alone, changed attributes are updated, nothing
is ever deleted. Rejected rows are printed and make the command exit non-zero.

Bootstrap creates the university root and the Super Admin; this creates
everything below the root (Faculty Divisions, Schools, Departments, Programmes).
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import cast

from unicore.core.db import get_sessionmaker
from unicore.core.logging import configure_logging, get_logger
from unicore.core.security import AuthContext
from unicore.modules.org import dao as org_dao
from unicore.modules.org import service as org_service
from unicore.modules.org.models import OrgUnit
from unicore.modules.org.schemas import OrgUnitCreate

DEFAULT_CATALOGUE = Path(__file__).resolve().parents[2] / "seeds" / "takshashila_university.csv"
DEFAULT_UNIVERSITY_NAME = "Takshashila University"
DEFAULT_UNIVERSITY_CODE = "TU"

_SEED_CTX = AuthContext(user_id="seed", session_id="seed", role_names=("super-admin",))

_COUNT_ORDER = ("university", "faculty_division", "school", "department", "program", "section")


async def run(catalogue: Path, university_name: str, university_code: str) -> int:
    """Returns the number of rejected rows (0 = a clean seed)."""
    log = get_logger()
    content = catalogue.read_bytes()

    async with get_sessionmaker()() as session:
        root = await org_dao.get_root(session)
        if root is None:
            root = await org_service.create_unit(
                session,
                _SEED_CTX,
                OrgUnitCreate(type="university", name=university_name, code=university_code),
            )
            log.info("university root created", org_unit_id=str(root.id), code=root.code)
        elif root.name != university_name:
            # The code is immutable — it is the first label of every descendant
            # path — so only the display name is reconciled here.
            previous = root.name
            root = await org_service.rename_unit(session, _SEED_CTX, root.id, university_name)
            log.info("university root renamed", previous=previous, name=root.name, code=root.code)

        result = await org_service.import_csv(session, _SEED_CTX, catalogue.name, content)
        units = await org_service.list_units(session, None, None, True, 10_000)

    rejected = int(str(result["rows_rejected"]))
    _report(root.name, root.code, catalogue, result, units)
    log.info(
        "seed completed",
        catalogue=catalogue.name,
        units_created=result["rows_created"],
        units_updated=result["rows_updated"],
        rows_rejected=rejected,
    )
    return rejected


def _report(
    university_name: str,
    university_code: str,
    catalogue: Path,
    result: dict[str, object],
    units: list[OrgUnit],
) -> None:
    counts: dict[str, int] = {}
    for unit in units:
        counts[unit.type] = counts.get(unit.type, 0) + 1

    out = sys.stdout.write
    out(f"\nSeeded {university_name} ({university_code}) from {catalogue.name}\n")
    # The importer counts unit *visits* (one row touches 4 levels), not CSV rows.
    out(
        f"  units touched {result['rows_total']} · created {result['rows_created']}"
        f" · updated {result['rows_updated']} · unchanged {result['rows_unchanged']}"
        f" · rows rejected {result['rows_rejected']}\n"
    )
    out("  structure now: " + " · ".join(
        f"{unit_type.replace('_', ' ')} {counts[unit_type]}"
        for unit_type in _COUNT_ORDER
        if unit_type in counts
    ) + "\n")

    errors = cast(list[dict[str, object]], result.get("errors") or [])
    if errors:
        out(f"\n  {len(errors)} row(s) rejected — first 10:\n")
        for error in errors[:10]:
            out(f"    row {error['row_number']}: {error['field']} — {error['reason']}\n")
    out("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path, default=DEFAULT_CATALOGUE)
    parser.add_argument("--university-name", default=DEFAULT_UNIVERSITY_NAME)
    parser.add_argument("--university-code", default=DEFAULT_UNIVERSITY_CODE)
    args = parser.parse_args()

    if not args.catalogue.is_file():
        parser.error(f"catalogue not found: {args.catalogue}")

    configure_logging("unicore-seed")
    rejected = asyncio.run(run(args.catalogue, args.university_name, args.university_code))
    sys.exit(1 if rejected else 0)


if __name__ == "__main__":
    main()
