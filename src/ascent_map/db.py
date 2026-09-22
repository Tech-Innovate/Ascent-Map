from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class DuckDBUnavailable(RuntimeError):
    pass


def _duckdb():
    try:
        import duckdb  # type: ignore
    except ImportError as exc:
        raise DuckDBUnavailable(
            "DuckDB is not installed. Install the project with: pip install -e ."
        ) from exc
    return duckdb


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return _duckdb().connect(str(self.path))

    def initialize(self, schema_path: Path) -> None:
        schema = schema_path.read_text(encoding="utf-8")
        with self.connect() as con:
            con.execute(schema)


def rows_as_dicts(cursor) -> list[dict[str, Any]]:
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def decode_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list, bool, int, float)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
