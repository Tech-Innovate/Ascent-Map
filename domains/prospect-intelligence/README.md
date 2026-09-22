# Prospect Intelligence

Local-first domain for turning public business observations into auditable organization profiles, service matches, and communication-channel suitability assessments.

## Decision chain

```text
Observed Fact
    ↓
Evidence
    ↓
Resolved Fact
    ↓
Derived Signal
    ↓
Profile Snapshot
    ↓
Service Match / Channel Match
    ↓
Human-reviewed decision support
```

Acquisition tools are adapters. They do not own business truth or commercial decisions.

## V0.3 local executable

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'

ascent-map init-db
ascent-map ingest-maps path/to/maps-results.json
ascent-map enrich-web --max-pages 3     # optional network enrichment
ascent-map resolve-entities             # optional inspection stage
ascent-map evaluate
ascent-map show
```

Maps-only:

```bash
ascent-map run path/to/maps-results.json
```

Maps plus explicit official-website enrichment:

```bash
ascent-map run path/to/maps-results.json --enrich-web --max-pages 3
```

`evaluate` refreshes organization resolution and review-topic evidence automatically. Use `--json` with `show` or `run` for machine-readable output.

## V0.3 identity model

A Google Maps listing is a **source/listing entity**, not automatically an organization. Each Maps listing keeps a stable, distinct `business_id` based on its source identity. Organization grouping happens later and is non-destructive.

Current strong linking evidence is deliberately narrow:

- same non-shared official domain plus sufficiently similar organization names;
- same normalized public phone plus sufficiently similar organization names;
- official domain + phone overlap.

Shared domain/phone evidence without enough name agreement becomes an `entity_resolution_edge` with `decision=candidate` instead of an automatic merge.

Profiles are evaluated once per resolved organization representative. `profile.identity.member_business_ids` records the listings/branches contributing to that organization.

## V0.3 review intelligence

If the Maps input contains `user_reviews` or `user_reviews_extended`, the pipeline normalizes review observations and evaluates deterministic topic rules for:

- slow response;
- unanswered contacts;
- booking difficulty;
- waiting-time friction;
- delivery problems.

Review-topic evidence can support:

- `responsiveness_gap`;
- `complaint_intensity`;
- `customer_experience_gap`.

It does **not** infer `manual_process_indicator`; customer complaints do not establish the internal implementation of a workflow.

### Privacy minimization

Normalized review intelligence intentionally excludes reviewer names, profile URLs, and avatar URLs. The retained fields are limited to review-level evidence useful for business analysis: source review ID when present, rating, review text, language, publication timing, and owner-reply presence.

The immutable raw Maps artifact remains the provenance source.

### Temporal semantics

`review_topic_summary` is historical. On every evaluation, prior active derived topic evidence is superseded and only the latest topic state participates in current fact resolution. This prevents an earlier high complaint prevalence from permanently dominating a later, larger review sample.

## Official website enrichment

Website enrichment remains opt-in and public-site-only.

- Default maximum: 3 pages per listing website.
- Same-site bounded secondary crawl.
- `robots.txt` allow/disallow rules respected when retrievable.
- Localhost, private, link-local, reserved, credential-bearing, and non-HTTP(S) targets rejected.
- Redirect targets validated before following.
- Response size bounded.
- Repeated predicate/value observations from the same listing/site source are not treated as independent corroboration.

A shared organization domain can appear on several branch listings; website provenance stays attached to the listing being enriched. Organization resolution combines those sources later.

Current website observations include contact channels, booking, ecommerce/checkout, customer portals, social links, language support, HTTPS, and mobile viewport support. Missing capabilities remain `unknown` unless evidence directly establishes absence.

## Confidence semantics

Current fact confidence is based on evidence quality and **independent source entities**. Repeated observations from the same source entity do not inflate confidence.

Service/channel evaluation keeps these concepts separate:

```text
fit / suitability
coverage of relevant evidence
evidence confidence
```

Failed hard prerequisites preserve the confidence of the evidence that failed the prerequisite; they are not assigned artificial 100% confidence.

## Pre-V0.3 database compatibility

V0.2 used a domain-derived `business_id` when a website was available. That could collapse multiple listings before a separate organization layer existed. Because a destructive merge cannot be split reliably from the collapsed database alone, V0.2 local DuckDB files should be recreated and raw Maps data re-ingested.

Raw artifacts under `.ascent-map/raw/` may be retained.

## Domain files

- `config/signals.yaml` — canonical signal vocabulary and input dependencies.
- `config/services.yaml` — service portfolio definitions and fit rules.
- `config/channels.yaml` — communication-channel suitability rules.
- `config/maps-field-mapping.yaml` — Maps output to canonical evidence mapping.
- `db/schema.sql` — DuckDB identity/evidence/profile/evaluation schema.
- `tools/validate_config.py` — configuration cross-validation.

Runtime implementation lives in `src/ascent_map/prospect/`; tests live in `tests/`.

## Evidence states

Do not conflate "not observed" with `false`.

- `present`
- `absent`
- `unknown`
- `conflicting`
- `not_applicable`

## Implementation rules

- Acquisition adapters create evidence, not recommendations.
- Listing identity and organization resolution are separate layers.
- The service evaluator must not create facts.
- The profile builder must not alter raw evidence.
- Review complaints must not be promoted into unsupported internal-process claims.
- LLM-derived observations must retain evidence references and confidence.
- Communication-channel selection is independent from service selection.
- Public customer channels must not be assumed to be procurement or executive channels.
- Raw acquisition artifacts remain immutable and content-addressed.

## Validation

```bash
python domains/prospect-intelligence/tools/validate_config.py
pytest -q
```

GitHub Actions runs configuration validation and deterministic unit/integration tests. Website tests use fake fetchers and do not access the public Internet.
