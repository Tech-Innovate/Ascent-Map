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

## V0.1 local executable

The repository now contains a runnable Python pipeline backed by DuckDB.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e '.[dev]'

ascent-map init-db
ascent-map ingest-maps path/to/maps-results.json
ascent-map evaluate
ascent-map show
```

Or run the complete local path in one command:

```bash
ascent-map run path/to/maps-results.json
```

Use `--json` with `show` or `run` for a machine-readable report.

The default database and immutable raw-artifact store live under `.ascent-map/`, which is intentionally excluded from Git.

## What V0.1 does

1. Accepts `gosom/google-maps-scraper` JSON arrays, single JSON objects, or JSONL.
2. Copies the source payload into a content-addressed local raw-artifact store.
3. Resolves stable business, location, and source identities.
4. Converts source fields into canonical evidence predicates.
5. Resolves current facts while retaining contradictory observations.
6. Derives Maps-supported baseline signals.
7. Creates an immutable, versioned profile snapshot.
8. Evaluates the configured service portfolio.
9. Evaluates communication channels independently of service fit.
10. Produces an explainable local report with fit and evidence confidence kept separate.

V0.1 intentionally leaves website-only, social, review-topic, and manual-process signals as `unknown` until those enrichment adapters exist.

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

GitHub Actions runs both checks, including the DuckDB end-to-end pipeline test.
