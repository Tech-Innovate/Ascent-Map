# Ascent Map

Ascent Map is a local-first intelligence and decision-support system for turning heterogeneous evidence into explainable profiles, evaluations, and actions.

The project is organized around explicit architectural boundaries rather than around any single data source or business workflow. External systems such as Google Maps are adapters: they provide evidence, but they do not own canonical truth, profiles, or decisions.

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

These are logical ownership boundaries. They do not require separate services or processes.

## First domain: Prospect Intelligence

The first implemented domain uses public business evidence to build auditable potential-client profiles and evaluate:

- observable business characteristics;
- digital and operational maturity;
- customer-interaction patterns;
- service-portfolio fit;
- communication-channel suitability;
- evidence confidence and unresolved questions.

The governing chain is:

```text
Observed fact
    -> Evidence
    -> Resolved fact
    -> Derived signal
    -> Profile snapshot
    -> Service / channel evaluation
    -> Human-reviewed decision support
```

A scraped value is evidence. A profile attribute is a derived interpretation. A service match is an evaluation against that profile. These layers must remain separate and traceable.

See `domains/prospect-intelligence/` for the first domain implementation.

## Repository layout

```text
Ascent-Map/
├── docs/
│   ├── architecture/
│   └── adr/
├── domains/
│   └── prospect-intelligence/
├── src/
│   ├── state/
│   ├── memory/
│   ├── context/
│   ├── reasoning/
│   ├── agency/
│   ├── evaluation/
│   └── adapters/
└── tests/
```

## Principles

1. **Evidence before inference** — derived claims must retain provenance.
2. **Unknown is not false** — absence, uncertainty, conflict, and inapplicability are distinct states.
3. **Immutable historical state** — profiles and evaluations are versioned snapshots.
4. **Separate fit from confidence** — a high apparent fit with weak evidence is not equivalent to a high-confidence fit.
5. **Adapters do not own truth** — source schemas are normalized at the boundary.
6. **LLMs produce claims, not silent mutations** — semantic interpretation must remain attributable and reviewable.
7. **Human agency at decision boundaries** — evaluations inform decisions; they do not silently execute consequential outreach or commitments.

## Status

Early architecture and first-domain bootstrap.

## License

To be defined before the first public release.
