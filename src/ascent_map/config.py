from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    domain: Path
    config: Path
    schema: Path


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        marker = candidate / "domains" / "prospect-intelligence" / "config"
        if marker.is_dir():
            return candidate
    raise FileNotFoundError(
        "Could not locate Ascent Map project root. Run inside the repository or pass --project-root."
    )


def project_paths(root: Path | None = None) -> ProjectPaths:
    base = (root or find_project_root()).resolve()
    domain = base / "domains" / "prospect-intelligence"
    return ProjectPaths(
        root=base,
        domain=domain,
        config=domain / "config",
        schema=domain / "db" / "schema.sql",
    )


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data
