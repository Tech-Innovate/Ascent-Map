# Prospect Intelligence

Local-first domain for turning public business observations into auditable profiles, service matches, and communication-channel suitability assessments.

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

## V0.2 local executable

The repository contains a runnable Python pipeline backed by DuckDB.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'

ascent-map init-db
ascent-map ingest-maps path/to/maps-results.json
ascent-map enrich-web --max-pages 3
ascent-map evaluate
ascent-map show
```

For a Maps-only path with no website network acquisition:

```bash
ascent-map run path/to/maps-results.json
```

For Maps plus explicit official-website enrichment:

```bash
ascent-map run path/to/maps-results.json --enrich-web --max-pages 3
```

Use `--json` with `show` or `run` for a machine-readable report. The default database and immutable raw-artifact store live under `.ascent-map/`, which is intentionally excluded from Git.

## What V0.2 does

1. Accepts `gosom/google-maps-scraper` JSON arrays, single JSON objects, or JSONL.
2. Copies the Maps source payload into a content-addressed local raw-artifact store.
3. Resolves stable business, location, and source identities.
4. Converts Maps fields into canonical evidence predicates.
5. Optionally performs a bounded crawl of the canonical official website.
6. Preserves fetched HTML as content-addressed evidence under `.ascent-map/raw/web/`.
7. Extracts observable website facts for contact channels, booking, ecommerce, portals, social links, language support, HTTPS, and mobile readiness.
8. Resolves current facts while retaining contradictory observations.
9. Derives Maps- and website-supported profile signals.
10. Creates immutable, versioned profile snapshots.
11. Evaluates the configured service portfolio.
12. Evaluates communication channels independently of service fit.
13. Produces an explainable local report with fit, evidence coverage, and evidence confidence kept separate.

Review-topic interpretation and manual-process claims remain `unknown` until dedicated evidence exists.

## Website acquisition boundaries

Website enrichment is explicit, bounded, and public-site-only.

- Default maximum: 3 pages per official website.
- Crawl candidates are restricted to the same normalized hostname.
- `robots.txt` allow/disallow path rules are respected when retrievable.
- Localhost, private, link-local, reserved, and other non-public IP targets are rejected.
- Credential-bearing URLs and non-HTTP(S) schemes are rejected.
- Redirect targets are validated before following.
- Response size is bounded.
- Identical predicate/value observations from multiple pages of the same website are deduplicated at the source-entity level.

The adapter generally records positive observations. A feature not seen on the sampled pages remains `unknown`; it is not silently converted to `false`.

## Website-derived evidence and signals

Current observations include:

- `contact.whatsapp.*`
- `contact.email.*`
- `contact.phone.*`
- `contact.form.state`
- `contact.instagram.*`
- `contact.facebook.*`
- `contact.linkedin.*`
- `website.booking_present`
- `website.ecommerce_present`
- `website.checkout_present`
- `website.customer_portal_present`
- `website.languages`
- `digital.https`
- `digital.mobile_ready`
- `digital.multilingual`

Those facts can strengthen canonical signals such as:

- `whatsapp_presence`
- `contact_form_presence`
- `social_messaging_presence`
- `appointment_driven`
- `online_booking`
- `transaction_driven`
- `ecommerce_capability`
- `customer_portal`
- `multilingual_digital_presence`
- `digital_maturity`

## Domain files

- `config/signals.yaml` — canonical signal vocabulary and input dependencies.
- `config/services.yaml` — service portfolio definitions and fit rules.
- `config/channels.yaml` — communication-channel suitability rules.
- `config/maps-field-mapping.yaml` — Google Maps output to canonical evidence mapping.
- `db/schema.sql` — DuckDB schema for identity, evidence, facts, profiles, and evaluations.
- `examples/profile.json` — example profile snapshot.
- `tools/validate_config.py` — cross-validation for configuration references.

The executable implementation lives in `src/ascent_map/` and regression/integration tests live in `tests/`.

## Evidence states

Do not conflate "not observed" with `false`.

- `present`
- `absent`
- `unknown`
- `conflicting`
- `not_applicable`

## Evaluation semantics

A service or channel evaluation preserves three independent concepts:

```text
fit / suitability
coverage of relevant evidence
evidence confidence
```

Unknown rules do not silently become false. The rule trace is retained with each evaluation.

Default service classification:

- `< 0.50`: low fit
- `0.50–0.69`: candidate
- `0.70–0.84`: good fit
- `>= 0.85`: strong fit

Hard prerequisites can mark an organization `ineligible`; insufficient evidence remains a separate state.

## Implementation rules

- Acquisition adapters create evidence, not service recommendations.
- The service evaluator must not create facts.
- The profile builder must not alter raw evidence.
- LLM-derived observations must retain evidence references and confidence.
- Communication-channel selection is evaluated independently from service selection.
- Public customer channels must not be assumed to be procurement or executive channels.
- Raw acquisition artifacts remain immutable and content-addressed.

## Validation

Run:

```bash
python domains/prospect-intelligence/tools/validate_config.py
pytest -q
```

GitHub Actions runs both checks, including deterministic DuckDB Maps and website-enrichment integration tests.
