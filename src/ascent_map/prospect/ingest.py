from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class ParsedBusiness:
    business_id: str
    canonical_name: str
    normalized_name: str
    location_id: str
    source_entity_id: str
    external_id_type: str
    external_id: str
    source_url: str | None
    predicates: dict[str, Any]


def stable_id(prefix: str, *parts: Any, length: int = 20) -> str:
    payload = "|".join("" if part is None else str(part) for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def normalize_name(value: str) -> str:
    lowered = value.casefold().strip()
    lowered = re.sub(r"[^\w\s]", " ", lowered, flags=re.UNICODE)
    return re.sub(r"\s+", " ", lowered).strip()


def website_domain(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value if "://" in value else f"https://{value}"
    try:
        host = (urlparse(candidate).hostname or "").casefold()
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def read_maps_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        records: list[dict[str, Any]] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_no}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSONL line {line_no} must be an object")
            records.append(value)
        return records
    if isinstance(decoded, list):
        if not all(isinstance(item, dict) for item in decoded):
            raise ValueError("JSON array must contain objects")
        return decoded
    if isinstance(decoded, dict):
        return [decoded]
    raise ValueError("Maps input must be a JSON object, JSON array, or JSONL stream")


def get_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def is_meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _external_identity(record: dict[str, Any]) -> tuple[str, str]:
    for field in ("place_id", "cid", "data_id", "link"):
        value = record.get(field)
        if is_meaningful(value):
            return field, str(value)
    fallback = json.dumps(record, sort_keys=True, ensure_ascii=False)
    return "record_hash", hashlib.sha256(fallback.encode("utf-8")).hexdigest()


def parse_business(record: dict[str, Any], mapping: dict[str, str]) -> ParsedBusiness:
    title = str(record.get("title") or "").strip()
    if not title:
        raise ValueError("Maps record is missing title")
    normalized = normalize_name(title)
    website = record.get("web_site") or record.get("website")
    domain = website_domain(str(website)) if website else None
    ext_type, ext_value = _external_identity(record)
    business_key = f"domain:{domain}" if domain else f"listing:{ext_type}:{ext_value}"
    business_id = stable_id("biz", business_key)
    location_key = record.get("place_id") or record.get("cid") or (
        f"{record.get('address')}|{record.get('latitude')}|{record.get('longitude')}"
    )
    location_id = stable_id("loc", business_id, location_key)
    source_entity_id = stable_id("src", "google_maps", ext_type, ext_value)

    predicates: dict[str, Any] = {}
    for source_path, predicate in mapping.items():
        value = get_path(record, source_path)
        if is_meaningful(value):
            predicates[predicate] = value

    # Presence facts are emitted only when positively observed. Missing source
    # fields remain unknown; they are never converted to false by ingestion.
    if is_meaningful(website):
        predicates["digital.website_present"] = True
    if is_meaningful(record.get("phone")):
        predicates["contact.phone.state"] = "present"
        predicates["maps.phone_present"] = True
    if is_meaningful(record.get("emails")):
        predicates["contact.email.state"] = "present"
    if is_meaningful(record.get("reservations")):
        predicates["maps.reservations_present"] = True
    if is_meaningful(record.get("order_online")):
        predicates["maps.order_online_present"] = True
    if is_meaningful(record.get("menu")):
        predicates["maps.menu_present"] = True

    return ParsedBusiness(
        business_id=business_id,
        canonical_name=title,
        normalized_name=normalized,
        location_id=location_id,
        source_entity_id=source_entity_id,
        external_id_type=ext_type,
        external_id=ext_value,
        source_url=str(record.get("link")) if record.get("link") else None,
        predicates=predicates,
    )


def json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
