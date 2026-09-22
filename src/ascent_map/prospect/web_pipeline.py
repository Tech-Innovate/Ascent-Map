from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Callable

from ascent_map.config import ProjectPaths
from ascent_map.db import Database, decode_json

from .ingest import json_value, stable_id, utc_now, value_type, website_domain
from .web import WebEvidence, WebFetch, analyze_html, decode_html, fetch_website, robots_allowed, validate_public_url


def _website_targets(con, business_id: str | None = None) -> list[tuple[str, str]]:
    params: list[object] = []
    query = """SELECT business_id, value_json, collected_at
               FROM evidence
               WHERE predicate = 'digital.website.url' AND is_active = true"""
    if business_id:
        query += " AND business_id = ?"
        params.append(business_id)
    query += " ORDER BY business_id, collected_at DESC"
    rows = con.execute(query, params).fetchall()
    seen: set[str] = set()
    targets: list[tuple[str, str]] = []
    for biz_id, value_json, _ in rows:
        if biz_id in seen:
            continue
        value = decode_json(value_json)
        if isinstance(value, str) and value.strip():
            seen.add(biz_id)
            targets.append((biz_id, value.strip()))
    return targets


def _store_artifact(paths: ProjectPaths, fetched: WebFetch) -> tuple[str, str, str]:
    digest = hashlib.sha256(fetched.body).hexdigest()
    artifact_id = stable_id("art", "website", digest)
    raw_dir = paths.root / ".ascent-map" / "raw" / "web"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{digest}.html"
    if not raw_path.exists():
        raw_path.write_bytes(fetched.body)
    return artifact_id, digest, str(raw_path.relative_to(paths.root))


def _store_page_evidence(
    con,
    paths: ProjectPaths,
    business_id: str,
    source_entity_id: str,
    fetched: WebFetch,
    evidence: list[WebEvidence],
    collected: datetime,
) -> int:
    artifact_id, digest, storage_path = _store_artifact(paths, fetched)
    con.execute(
        """INSERT INTO raw_artifact
           (artifact_id, source_type, media_type, storage_path, content_sha256,
            collector, collector_version, collected_at)
           VALUES (?, 'official_website', ?, ?, ?, 'ascent-map/web', '0.3.0', ?)
           ON CONFLICT DO NOTHING""",
        [artifact_id, fetched.content_type, storage_path, digest, collected],
    )
    added = 0
    for item in evidence:
        encoded = json_value(item.value)
        # The same listing/site source observing the same predicate/value on
        # several pages is one corroborating source, not independent votes.
        evidence_id = stable_id("ev", source_entity_id, item.predicate, encoded)
        existed = con.execute("SELECT count(*) FROM evidence WHERE evidence_id = ?", [evidence_id]).fetchone()[0]
        con.execute(
            """INSERT INTO evidence
               (evidence_id, business_id, source_entity_id, artifact_id, predicate,
                value_json, value_type, observed_at, collected_at, source_reliability,
                directness, extraction_confidence, collector, collector_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.97, ?, ?, 'ascent-map/web', '0.3.0')
               ON CONFLICT DO NOTHING""",
            [
                evidence_id,
                business_id,
                source_entity_id,
                artifact_id,
                item.predicate,
                encoded,
                value_type(item.value),
                collected,
                collected,
                item.directness,
                item.extraction_confidence,
            ],
        )
        if not existed:
            added += 1
    return added


def enrich_websites(
    db: Database,
    paths: ProjectPaths,
    business_id: str | None = None,
    *,
    max_pages: int = 3,
    timeout: float = 12.0,
    delay: float = 0.25,
    fetcher: Callable[..., WebFetch] = fetch_website,
) -> dict[str, int]:
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    collected = utc_now()
    with db.connect() as con:
        targets = _website_targets(con, business_id)

    stats = {
        "businesses": len(targets),
        "sites_fetched": 0,
        "pages_fetched": 0,
        "evidence": 0,
        "robots_skipped": 0,
        "errors": 0,
    }

    for biz_id, website_url in targets:
        domain = website_domain(website_url)
        if not domain:
            stats["errors"] += 1
            continue
        root_source_url = website_url if "://" in website_url else f"https://{website_url}"
        try:
            validate_public_url(root_source_url)
        except ValueError:
            # Tests and fully offline adapters may intentionally use .invalid.
            if fetcher is fetch_website or not domain.endswith(".invalid"):
                stats["errors"] += 1
                continue

        # A domain can legitimately be shared by several branch listings. Keep
        # provenance attached to the listing being enriched; organization-level
        # corroboration is resolved later without violating source_entity FKs.
        external_id = f"{biz_id}:{domain}"
        source_entity_id = stable_id("src", "official_website", "business_domain", external_id)
        with db.connect() as con:
            con.execute(
                """INSERT INTO source_entity
                   (source_entity_id, business_id, source_type, external_id_type, external_id,
                    source_url, first_seen_at, last_seen_at)
                   VALUES (?, ?, 'official_website', 'business_domain', ?, ?, ?, ?)
                   ON CONFLICT DO NOTHING""",
                [source_entity_id, biz_id, external_id, root_source_url, collected, collected],
            )
            con.execute("UPDATE source_entity SET last_seen_at = ? WHERE source_entity_id = ?", [collected, source_entity_id])

        queue = [root_source_url]
        visited: set[str] = set()
        site_fetched = False
        while queue and len(visited) < max_pages:
            target = queue.pop(0).split("#", 1)[0]
            if target in visited:
                continue
            try:
                if not robots_allowed(root_source_url, target, fetcher=fetcher, timeout=min(timeout, 8.0)):
                    stats["robots_skipped"] += 1
                    visited.add(target)
                    continue
                fetched = fetcher(target, timeout=timeout)
                page_evidence, discovered = analyze_html(decode_html(fetched.body), fetched.final_url)
            except Exception:
                stats["errors"] += 1
                visited.add(target)
                continue

            visited.add(target)
            site_fetched = True
            stats["pages_fetched"] += 1
            with db.connect() as con:
                con.execute("BEGIN TRANSACTION")
                try:
                    stats["evidence"] += _store_page_evidence(
                        con, paths, biz_id, source_entity_id, fetched, page_evidence, collected
                    )
                    con.execute("COMMIT")
                except Exception:
                    con.execute("ROLLBACK")
                    raise

            for link in discovered:
                if len(visited) + len(queue) >= max_pages:
                    break
                if link not in visited and link not in queue:
                    queue.append(link)
            if delay > 0 and queue:
                time.sleep(delay)

        if site_fetched:
            stats["sites_fetched"] += 1
    return stats
