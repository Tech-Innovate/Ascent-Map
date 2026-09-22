-- Prospect Intelligence - DuckDB schema
-- Local-first canonical model.
-- Raw acquisition artifacts remain immutable; derived state is versioned.

CREATE SEQUENCE IF NOT EXISTS seq_business START 1;
CREATE SEQUENCE IF NOT EXISTS seq_location START 1;
CREATE SEQUENCE IF NOT EXISTS seq_source_entity START 1;
CREATE SEQUENCE IF NOT EXISTS seq_raw_artifact START 1;
CREATE SEQUENCE IF NOT EXISTS seq_evidence START 1;
CREATE SEQUENCE IF NOT EXISTS seq_resolved_fact START 1;
CREATE SEQUENCE IF NOT EXISTS seq_signal_fact START 1;
CREATE SEQUENCE IF NOT EXISTS seq_profile START 1;
CREATE SEQUENCE IF NOT EXISTS seq_service_match START 1;
CREATE SEQUENCE IF NOT EXISTS seq_contact_point START 1;
CREATE SEQUENCE IF NOT EXISTS seq_channel_match START 1;
CREATE SEQUENCE IF NOT EXISTS seq_assessment START 1;

CREATE TABLE IF NOT EXISTS business (
    business_id           VARCHAR PRIMARY KEY,
    canonical_name        VARCHAR NOT NULL,
    normalized_name       VARCHAR,
    organization_status   VARCHAR DEFAULT 'unknown',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS location (
    location_id           VARCHAR PRIMARY KEY,
    business_id           VARCHAR NOT NULL REFERENCES business(business_id),
    label                 VARCHAR,
    address_text          VARCHAR,
    street                VARCHAR,
    city                  VARCHAR,
    region                VARCHAR,
    postal_code           VARCHAR,
    country               VARCHAR,
    latitude              DOUBLE,
    longitude             DOUBLE,
    is_primary            BOOLEAN DEFAULT false,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS source_entity (
    source_entity_id      VARCHAR PRIMARY KEY,
    business_id           VARCHAR NOT NULL REFERENCES business(business_id),
    location_id           VARCHAR REFERENCES location(location_id),
    source_type           VARCHAR NOT NULL,
    external_id_type      VARCHAR,
    external_id           VARCHAR,
    source_url            VARCHAR,
    first_seen_at         TIMESTAMPTZ NOT NULL,
    last_seen_at          TIMESTAMPTZ NOT NULL,
    UNIQUE(source_type, external_id_type, external_id)
);

CREATE TABLE IF NOT EXISTS raw_artifact (
    artifact_id           VARCHAR PRIMARY KEY,
    business_id           VARCHAR REFERENCES business(business_id),
    source_entity_id      VARCHAR REFERENCES source_entity(source_entity_id),
    source_type           VARCHAR NOT NULL,
    media_type            VARCHAR NOT NULL,
    storage_path          VARCHAR NOT NULL,
    content_sha256        VARCHAR NOT NULL,
    collector             VARCHAR,
    collector_version     VARCHAR,
    collected_at          TIMESTAMPTZ NOT NULL,
    UNIQUE(content_sha256)
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id           VARCHAR PRIMARY KEY,
    business_id           VARCHAR NOT NULL REFERENCES business(business_id),
    location_id           VARCHAR REFERENCES location(location_id),
    source_entity_id      VARCHAR REFERENCES source_entity(source_entity_id),
    artifact_id           VARCHAR REFERENCES raw_artifact(artifact_id),

    predicate             VARCHAR NOT NULL,
    value_json            JSON NOT NULL,
    value_type            VARCHAR NOT NULL,

    observed_at           TIMESTAMPTZ,
    collected_at          TIMESTAMPTZ NOT NULL,

    source_reliability    DOUBLE CHECK(source_reliability BETWEEN 0 AND 1),
    directness            DOUBLE CHECK(directness BETWEEN 0 AND 1),
    extraction_confidence DOUBLE CHECK(extraction_confidence BETWEEN 0 AND 1),

    collector             VARCHAR,
    collector_version     VARCHAR,

    supersedes_evidence_id VARCHAR REFERENCES evidence(evidence_id),
    is_active             BOOLEAN NOT NULL DEFAULT true,
    notes                 VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_evidence_business_predicate
    ON evidence(business_id, predicate);

CREATE TABLE IF NOT EXISTS resolved_fact (
    fact_id               VARCHAR PRIMARY KEY,
    business_id           VARCHAR NOT NULL REFERENCES business(business_id),
    location_id           VARCHAR REFERENCES location(location_id),

    predicate             VARCHAR NOT NULL,
    resolved_value_json   JSON NOT NULL,
    state                 VARCHAR NOT NULL DEFAULT 'present',
    confidence            DOUBLE NOT NULL CHECK(confidence BETWEEN 0 AND 1),

    resolution_method     VARCHAR NOT NULL,
    rule_version          VARCHAR,
    evidence_ids_json     JSON NOT NULL,

    valid_from            TIMESTAMPTZ NOT NULL,
    valid_to              TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    CHECK(state IN ('present','absent','unknown','conflicting','not_applicable'))
);

CREATE INDEX IF NOT EXISTS idx_resolved_fact_business_predicate
    ON resolved_fact(business_id, predicate);

CREATE TABLE IF NOT EXISTS signal_definition (
    signal_id             VARCHAR PRIMARY KEY,
    domain                VARCHAR NOT NULL,
    signal_type           VARCHAR NOT NULL,
    description           VARCHAR,
    definition_version    VARCHAR NOT NULL,
    enabled               BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS signal_fact (
    signal_fact_id        VARCHAR PRIMARY KEY,
    business_id           VARCHAR NOT NULL REFERENCES business(business_id),
    profile_scope         VARCHAR NOT NULL DEFAULT 'organization',
    signal_id             VARCHAR NOT NULL,

    value_json            JSON NOT NULL,
    score                 DOUBLE CHECK(score BETWEEN 0 AND 1),
    confidence            DOUBLE NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    state                 VARCHAR NOT NULL DEFAULT 'present',

    method_type           VARCHAR NOT NULL,
    method_id             VARCHAR NOT NULL,
    method_version        VARCHAR NOT NULL,

    evidence_ids_json     JSON NOT NULL,
    explanation_json      JSON,

    evaluated_at          TIMESTAMPTZ NOT NULL,
    expires_at            TIMESTAMPTZ,

    CHECK(state IN ('present','absent','unknown','conflicting','not_applicable'))
);

CREATE INDEX IF NOT EXISTS idx_signal_business
    ON signal_fact(business_id, signal_id, evaluated_at);

CREATE TABLE IF NOT EXISTS profile_snapshot (
    profile_id            VARCHAR PRIMARY KEY,
    business_id           VARCHAR NOT NULL REFERENCES business(business_id),
    profile_version       INTEGER NOT NULL,
    as_of                 TIMESTAMPTZ NOT NULL,

    profile_json          JSON NOT NULL,
    completeness          DOUBLE CHECK(completeness BETWEEN 0 AND 1),
    freshness             DOUBLE CHECK(freshness BETWEEN 0 AND 1),
    contradiction_count   INTEGER NOT NULL DEFAULT 0,

    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(business_id, profile_version)
);

CREATE TABLE IF NOT EXISTS service_definition (
    service_id            VARCHAR NOT NULL,
    service_version       VARCHAR NOT NULL,
    name                  VARCHAR NOT NULL,
    portfolio             VARCHAR,
    definition_json       JSON NOT NULL,
    enabled               BOOLEAN NOT NULL DEFAULT true,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(service_id, service_version)
);

CREATE TABLE IF NOT EXISTS service_match (
    service_match_id      VARCHAR PRIMARY KEY,
    business_id           VARCHAR NOT NULL REFERENCES business(business_id),
    profile_id            VARCHAR NOT NULL REFERENCES profile_snapshot(profile_id),
    service_id            VARCHAR NOT NULL,
    service_version       VARCHAR NOT NULL,

    eligible              BOOLEAN NOT NULL DEFAULT true,
    status                VARCHAR NOT NULL,
    fit_score             DOUBLE CHECK(fit_score BETWEEN 0 AND 1),
    evidence_confidence   DOUBLE CHECK(evidence_confidence BETWEEN 0 AND 1),

    positive_factors_json JSON,
    limiting_factors_json JSON,
    uncertainties_json    JSON,
    rule_trace_json       JSON,

    evaluated_at          TIMESTAMPTZ NOT NULL,

    CHECK(status IN (
      'ineligible',
      'insufficient_evidence',
      'low_fit',
      'candidate',
      'good_fit',
      'strong_fit'
    ))
);

CREATE INDEX IF NOT EXISTS idx_service_match_business
    ON service_match(business_id, evaluated_at);

CREATE TABLE IF NOT EXISTS contact_point (
    contact_point_id      VARCHAR PRIMARY KEY,
    business_id           VARCHAR NOT NULL REFERENCES business(business_id),
    location_id           VARCHAR REFERENCES location(location_id),

    channel               VARCHAR NOT NULL,
    value                 VARCHAR,
    state                 VARCHAR NOT NULL,
    purpose_json          JSON,
    public_listed         BOOLEAN,
    source_evidence_id    VARCHAR REFERENCES evidence(evidence_id),
    confidence            DOUBLE CHECK(confidence BETWEEN 0 AND 1),

    first_seen_at         TIMESTAMPTZ,
    last_seen_at          TIMESTAMPTZ,

    CHECK(state IN ('present','absent','unknown','conflicting','not_applicable'))
);

CREATE TABLE IF NOT EXISTS channel_match (
    channel_match_id      VARCHAR PRIMARY KEY,
    business_id           VARCHAR NOT NULL REFERENCES business(business_id),
    profile_id            VARCHAR NOT NULL REFERENCES profile_snapshot(profile_id),

    channel_id            VARCHAR NOT NULL,
    purpose_class         VARCHAR,
    suitability           DOUBLE CHECK(suitability BETWEEN 0 AND 1),
    evidence_confidence   DOUBLE CHECK(evidence_confidence BETWEEN 0 AND 1),

    positive_factors_json JSON,
    cautions_json         JSON,
    rule_trace_json       JSON,

    requires_human_review BOOLEAN NOT NULL DEFAULT true,
    evaluated_at          TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS assessment (
    assessment_id         VARCHAR PRIMARY KEY,
    business_id           VARCHAR NOT NULL REFERENCES business(business_id),
    profile_id            VARCHAR REFERENCES profile_snapshot(profile_id),

    assessment_type       VARCHAR NOT NULL,
    status                VARCHAR NOT NULL DEFAULT 'open',
    analyst_note          VARCHAR,
    decision_json         JSON,

    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at           TIMESTAMPTZ
);

-- Optional queue for local processing stages.
CREATE TABLE IF NOT EXISTS pipeline_task (
    task_id               VARCHAR PRIMARY KEY,
    business_id           VARCHAR REFERENCES business(business_id),
    task_type             VARCHAR NOT NULL,
    task_payload_json     JSON,
    status                VARCHAR NOT NULL DEFAULT 'pending',
    attempts              INTEGER NOT NULL DEFAULT 0,
    available_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at            TIMESTAMPTZ,
    completed_at          TIMESTAMPTZ,
    last_error            VARCHAR,
    CHECK(status IN ('pending','running','completed','failed','cancelled'))
);

-- V0.3 organization resolution keeps source listings distinct. Organizations
-- are derived, reviewable groupings rather than destructive identity merges.
CREATE TABLE IF NOT EXISTS organization (
    organization_id              VARCHAR PRIMARY KEY,
    canonical_name               VARCHAR NOT NULL,
    normalized_name              VARCHAR,
    representative_business_id   VARCHAR NOT NULL REFERENCES business(business_id),
    resolution_confidence        DOUBLE NOT NULL CHECK(resolution_confidence BETWEEN 0 AND 1),
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS organization_member (
    business_id            VARCHAR PRIMARY KEY REFERENCES business(business_id),
    organization_id        VARCHAR NOT NULL REFERENCES organization(organization_id),
    relation               VARCHAR NOT NULL DEFAULT 'listing',
    confidence             DOUBLE NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    reasons_json           JSON NOT NULL,
    linked_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_organization_member_org
    ON organization_member(organization_id);

CREATE TABLE IF NOT EXISTS entity_resolution_edge (
    edge_id                VARCHAR PRIMARY KEY,
    left_business_id       VARCHAR NOT NULL REFERENCES business(business_id),
    right_business_id      VARCHAR NOT NULL REFERENCES business(business_id),
    score                  DOUBLE NOT NULL CHECK(score BETWEEN 0 AND 1),
    decision               VARCHAR NOT NULL,
    reasons_json           JSON NOT NULL,
    evaluated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK(decision IN ('linked','candidate','rejected')),
    UNIQUE(left_business_id, right_business_id)
);

-- Review normalization intentionally omits reviewer names/profile URLs. The
-- immutable raw Maps artifact remains available for provenance, while the
-- intelligence layer retains only content required for business-level analysis.
CREATE TABLE IF NOT EXISTS review_observation (
    review_observation_id  VARCHAR PRIMARY KEY,
    business_id            VARCHAR NOT NULL REFERENCES business(business_id),
    source_entity_id       VARCHAR REFERENCES source_entity(source_entity_id),
    artifact_id            VARCHAR REFERENCES raw_artifact(artifact_id),
    external_review_id     VARCHAR,
    source_field           VARCHAR NOT NULL,
    rating                 DOUBLE,
    text                   VARCHAR,
    language               VARCHAR,
    published_at           TIMESTAMPTZ,
    published_label        VARCHAR,
    has_owner_reply        BOOLEAN NOT NULL DEFAULT false,
    content_sha256         VARCHAR NOT NULL,
    collected_at           TIMESTAMPTZ NOT NULL,
    UNIQUE(business_id, content_sha256)
);

CREATE INDEX IF NOT EXISTS idx_review_observation_business
    ON review_observation(business_id, collected_at);

CREATE TABLE IF NOT EXISTS review_topic_summary (
    summary_id             VARCHAR PRIMARY KEY,
    business_id            VARCHAR NOT NULL REFERENCES business(business_id),
    topic_id               VARCHAR NOT NULL,
    score                  DOUBLE NOT NULL CHECK(score BETWEEN 0 AND 1),
    confidence             DOUBLE NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    sample_size            INTEGER NOT NULL,
    matched_count          INTEGER NOT NULL,
    review_ids_json        JSON NOT NULL,
    rule_version           VARCHAR NOT NULL,
    evaluated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_review_topic_business
    ON review_topic_summary(business_id, topic_id, evaluated_at);
