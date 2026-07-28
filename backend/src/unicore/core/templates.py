"""Shared CSV upload-template registry.

Templates are generated from the same column tuples the validators use, so a
schema change can never leave a stale template behind. Each rendered file has a
commented notes line, the header row, and one realistic example row.
"""

import csv
import io
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CsvTemplate:
    key: str
    title: str
    description: str
    columns: tuple[str, ...]
    examples: tuple[dict[str, str], ...]
    notes: tuple[str, ...] = field(default=())

    @property
    def filename(self) -> str:
        return f"unicore_{self.key}_template.csv"

    def render(self) -> str:
        buffer = io.StringIO()
        for note in self.notes:
            buffer.write(f"# {note}\n")
        writer = csv.writer(buffer)
        writer.writerow(self.columns)
        for example in self.examples:
            writer.writerow([example.get(column, "") for column in self.columns])
        return buffer.getvalue()


_REGISTRY: dict[str, CsvTemplate] = {}


def register(template: CsvTemplate) -> CsvTemplate:
    _REGISTRY[template.key] = template
    return template


def get(key: str) -> CsvTemplate | None:
    return _REGISTRY.get(key)


def all_templates() -> list[CsvTemplate]:
    return sorted(_REGISTRY.values(), key=lambda t: t.key)
