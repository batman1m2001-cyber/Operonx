-- Schema version 4: enrichment (PLAN §9).
-- kb_enrichment_cache holds every model answer an ingest stage paid for, keyed by
-- what it was asked (enricher_fp: the stage, its prompt version, settings and
-- model; input_sha: the hash of the request's messages), with the call's tokens
-- and cost. A stage asks the model only for inputs this table lacks.
-- kb_tree_nodes is a version's tree index (sections with summaries), committed
-- with the version; a node's span points into the version's canonical text.

CREATE TABLE kb_enrichment_cache (
    enricher_fp        TEXT NOT NULL,
    input_sha          TEXT NOT NULL,
    kind               TEXT NOT NULL,
    value              TEXT NOT NULL,
    model              TEXT,
    prompt_tokens      INTEGER NOT NULL DEFAULT 0,
    completion_tokens  INTEGER NOT NULL DEFAULT 0,
    cached_tokens      INTEGER NOT NULL DEFAULT 0,
    cost_usd           DOUBLE PRECISION,
    created_at         TEXT NOT NULL,
    PRIMARY KEY (enricher_fp, input_sha)
);

CREATE TABLE kb_tree_nodes (
    version_id   TEXT NOT NULL REFERENCES kb_versions(id),
    id           TEXT NOT NULL,
    parent_id    TEXT,
    path         TEXT NOT NULL,
    ordinal      INTEGER NOT NULL,
    depth        INTEGER NOT NULL,
    title        TEXT NOT NULL,
    span_start   INTEGER NOT NULL,
    span_end     INTEGER NOT NULL,
    pages        TEXT NOT NULL DEFAULT '[]',
    source       TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    summary_sha  TEXT,
    PRIMARY KEY (version_id, id)
);
