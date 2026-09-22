# Prospect Intelligence

Local-first domain for turning public business observations into auditable prospect profiles, service matches, and communication-channel recommendations.

## Design principle

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

## Files

- `config/signals.yaml` — canonical signal vocabulary and input dependencies.
- `config/services.yaml` — service portfolio definitions and fit rules.
- `config/channels.yaml` — communication-channel suitability rules.
- `config/maps-field-mapping.yaml` — mapping from `gosom/google-maps-scraper` output into canonical predicates.
- `db/schema.sql` — DuckDB schema for evidence, facts, profiles, and evaluations.
- `examples/profile.json` — example versioned profile snapshot.
- `tools/validate_config.py` — cross-validation for signal/service/channel references.

## Recommended local flow

1. Run `gosom/google-maps-scraper` with JSON output.
2. Preserve raw output unchanged as an immutable source artifact.
3. Create or resolve business identity.
4. Convert source fields into canonical evidence predicates.
5. Resolve competing observations into canonical facts.
6. Evaluate signal definitions.
7. Create an immutable profile snapshot.
8. Evaluate enabled portfolio services.
9. Evaluate communication channels independently.
10. Present fit, confidence, uncertainty, and provenance for human review.

## Evidence states

Do not conflate "not observed" with `false`.

Use:

- `present`
- `absent`
- `unknown`
- `conflicting`
- `not_applicable`

## Scoring

For a positive service or channel rule:

```text
contribution = match_strength × weight × signal_confidence
```

Normalize by the sum of applicable weights. Preserve two outputs:

```text
fit_score / suitability
evidence_confidence
```

Never collapse them into one score.

## Service classification defaults

- `< 0.50`: low fit
- `0.50–0.69`: candidate
- `0.70–0.84`: good fit
- `>= 0.85`: strong fit

Hard gates can still mark an organization `ineligible` or `insufficient_evidence`.

## Important implementation rules

- The service evaluator must not create facts.
- The profile builder must not alter raw evidence.
- LLM-derived observations must retain evidence references and confidence.
- Communication-channel selection is evaluated independently from service selection.
- Public customer channels must not be assumed to be procurement or executive channels.

## V1 execution shape

```text
ingest -> enrich -> resolve -> evaluate -> show
```

There is no need for distributed infrastructure for V1.
