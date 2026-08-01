"""Shared fixtures for ONB tests: an org tree with an approved term and Sections."""




def csv_bytes(rows: list[dict[str, str]]) -> bytes:
    from unicore.modules.onboarding.schemas import CSV_COLUMNS_V1

    header = ",".join(CSV_COLUMNS_V1)
    lines = [header]
    for row in rows:
        lines.append(",".join(row.get(col, "") for col in CSV_COLUMNS_V1))
    return ("\n".join(lines) + "\n").encode()


def student_row(n: int, **overrides: str) -> dict[str, str]:
    row = {
        "sif_id": f"SIF-{n:05d}",
        "full_name": f"Student {n}",
        "date_of_birth": "15-08-2006",
        "gender": "F",
        "mobile": f"90000{n:05d}",
        "email": f"student{n}@uni.example",
        "program_code": "BT-CSE",
        "section_label": "3A",
        "admission_year": "2026",
        "roll_number": f"R-{n:04d}",
    }
    row.update(overrides)
    return row
