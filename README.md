# Ascent Map

Ascent Map is a local-first intelligence and decision-support system for turning heterogeneous evidence into explainable profiles, evaluations, and actions.

External systems such as Google Maps and official business websites are adapters: they provide evidence, but they do not own canonical truth, profiles, or decisions.

## Architectural model

```text
External sources / tools
        |
        v
   Adapter Plane
        |
        v
+-------------------+
| State Engine      |  identity, evidence, claims, provenance, temporal state
+-------------------+
        |
        +--------------------+
        |                    |
        v                    v
+-------------------+  +-------------------+
| Memory Engine     |  | Context Engine    |
+-------------------+  +-------------------+
                              |
                              v
                       +-------------------+
                       | Reasoning Engine  |
                       +-------------------+
                              |
                    +---------+---------+
                    |                   |
                    v                   v
             +-------------+     +-------------------+
             | Agency      |     | Evaluation Engine |
             +-------------+     +-------------------+
```

These are logical ownership boundaries, not deployment requirements.

## First domain: Prospect Intelligence

The first domain turns public business evidence into auditable potential-client profiles and evaluates service-portfolio fit and communication-channel suitability.

```text
Observed fact
    -> Evidence
    -> Resolved fact
    -> Derived signal
    -> Profile snapshot
    -> Service / channel evaluation
    -> Human-reviewed decision support
```

A scraped value is evidence. A profile attribute is an interpretation. A service match is an evaluation against that profile. The layers remain separate and traceable.

## V0.3 quick start

Requirements: Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'

# Maps-only local path; performs no website network requests.
ascent-map run path/to/google-maps-results.json

# Explicitly enable official-website enrichment before evaluation.
ascent-map run path/to/google-maps-results.json --enrich-web
```

Individual stages are available as well:

```bash
ascent-map init-db
ascent-map ingest-maps path/to/results.json
ascent-map enrich-web --max-pages 3
ascent-map resolve-entities
ascent-map evaluate
ascent-map show
```

`evaluate` always refreshes organization resolution before building profiles, so `resolve-entities` is optional and exists mainly for inspection/debugging.

## V0.3 identity model

Google Maps listings remain distinct source/business entities at ingestion. They are **not** destructively merged just because they share a website domain.

Ascent Map then builds a separate organization layer using explicit evidence such as:

- shared official domain;
- normalized public phone overlap;
- name similarity.

High-confidence relationships become organization memberships. Weaker shared-domain/phone relationships remain `candidate` edges for human review instead of being force-merged.

This gives the system a clean distinction:

```text
Organization
    ├── Maps listing / branch A
    ├── Maps listing / branch B
    └── Maps listing / branch C
```

Profiles and service/channel evaluations are produced once per resolved organization while branch/source provenance remains intact.

## V0.3 review intelligence

When the Maps input contains `user_reviews` or `user_reviews_extended`, Ascent Map normalizes review content and can derive conservative business-level topic evidence for:

- slow response / unanswered contacts;
- booking difficulty;
- waiting-time friction;
- delivery problems.

Those observations can support `responsiveness_gap`, `complaint_intensity`, and `customer_experience_gap`. They **do not** prove internal workflow facts such as `manual_process_indicator`; unsupported operational claims remain unknown.

Reviewer names and profile/avatar URLs are intentionally excluded from the normalized review-intelligence tables. Raw source artifacts remain content-addressed for provenance.

Review-topic evidence is temporal: historical topic summaries remain auditable, while only the latest derived topic state participates in current fact resolution.

## Official website evidence

Website enrichment is opt-in. It performs a small, bounded crawl of the canonical public website, respects applicable `robots.txt` path rules, stores fetched HTML under `.ascent-map/raw/web/`, and rejects localhost/private/link-local/reserved targets and unsafe redirects.

The website adapter can observe evidence for:

- WhatsApp, public email, phone, and contact forms;
- booking/appointment flows;
- ecommerce/checkout indicators;
- customer portals;
- Instagram, Facebook, and LinkedIn links;
- multilingual support;
- HTTPS and mobile viewport support;
- richer digital-maturity inputs.

It does **not** infer missing capabilities as false. Absence on a crawled page normally remains unknown.

## Pre-release V0.2 database note

V0.3 corrects the pre-release identity model. V0.2 could derive a `business_id` directly from the website domain, which could collapse multiple listings before organization resolution existed.

Because that lost distinction cannot be reconstructed reliably from the collapsed database alone, V0.2 local databases should be recreated and the raw Maps data re-ingested:

```bash
rm .ascent-map/ascent-map.duckdb   # Windows: remove the same file manually/PowerShell
ascent-map run path/to/google-maps-results.json
```

Raw artifacts under `.ascent-map/raw/` do not need to be deleted.

## Repository layout

```text
Ascent-Map/
├── .github/workflows/ci.yml
├── docs/
│   └── architecture/
├── domains/
│   └── prospect-intelligence/
│       ├── config/
│       ├── db/
│       ├── examples/
│       └── tools/
├── src/
│   └── ascent_map/
│       └── prospect/
└── tests/
```

## Principles

1. **Evidence before inference** — derived claims retain provenance.
2. **Unknown is not false** — absence, uncertainty, conflict, and inapplicability are distinct states.
3. **Immutable historical state** — profiles and evaluations are versioned snapshots.
4. **Separate fit from confidence** — apparent fit and evidence quality are different dimensions.
5. **Adapters do not own truth** — source schemas are normalized at the boundary.
6. **LLMs produce claims, not silent mutations** — semantic interpretation remains attributable and reviewable.
7. **Human agency at decision boundaries** — evaluations inform decisions; they do not silently execute consequential outreach or commitments.

## Validation

```bash
python domains/prospect-intelligence/tools/validate_config.py
pytest -q
```

GitHub Actions runs configuration validation plus unit and DuckDB integration tests. Website tests use deterministic fake fetchers and do not call the public Internet.

## Status

V0.3 local Prospect Intelligence pipeline is under active bootstrap development.

## License

To be defined before the first public release.
