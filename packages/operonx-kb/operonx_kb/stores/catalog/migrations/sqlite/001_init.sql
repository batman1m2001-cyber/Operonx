-- operonx-kb catalog, schema version 1: the store of record (track5 §12).
-- Element and chunk spans point into the version's canonical text, which lives
-- in the blob store under text_sha. JSON columns hold lists and dicts.

CREATE TABLE kb_collections (
    id          TEXT PRIMARY KEY,
    spec        TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL
);

CREATE TABLE kb_documents (
    id                 TEXT PRIMARY KEY,
    collection_id      TEXT NOT NULL REFERENCES kb_collections(id),
    key                TEXT NOT NULL,
    title              TEXT,
    mime               TEXT NOT NULL,
    tags               TEXT NOT NULL DEFAULT '[]',
    metadata           TEXT NOT NULL DEFAULT '{}',
    active_version_id  TEXT,
    deleted_at         TEXT,
    created_at         TEXT NOT NULL,
    UNIQUE (collection_id, key)
);

CREATE TABLE kb_versions (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES kb_documents(id),
    ordinal      INTEGER NOT NULL,
    raw_sha      TEXT NOT NULL,
    text_sha     TEXT NOT NULL,
    pipeline_fp  TEXT NOT NULL,
    status       TEXT NOT NULL,
    stats        TEXT NOT NULL DEFAULT '{}',
    error        TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX kb_versions_document ON kb_versions (document_id);

CREATE TABLE kb_pages (
    version_id  TEXT NOT NULL REFERENCES kb_versions(id),
    page_no     INTEGER NOT NULL,
    width       REAL NOT NULL,
    height      REAL NOT NULL,
    unit        TEXT NOT NULL,
    image_sha   TEXT,
    text_layer  INTEGER NOT NULL,
    PRIMARY KEY (version_id, page_no)
);

-- `text` is stored for furniture only: body text is canonical[span].
CREATE TABLE kb_elements (
    version_id   TEXT NOT NULL REFERENCES kb_versions(id),
    id           TEXT NOT NULL,
    parent_id    TEXT,
    path         TEXT NOT NULL,
    ordinal      INTEGER NOT NULL,
    depth        INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    layer        TEXT NOT NULL,
    level        INTEGER,
    span_start   INTEGER,
    span_end     INTEGER,
    text         TEXT,
    regions      TEXT NOT NULL DEFAULT '[]',
    attrs        TEXT NOT NULL DEFAULT '{}',
    content_sha  TEXT NOT NULL,
    confidence   REAL,
    PRIMARY KEY (version_id, id)
);

CREATE TABLE kb_chunks (
    id              TEXT PRIMARY KEY,
    document_id     TEXT NOT NULL REFERENCES kb_documents(id),
    content_sha     TEXT NOT NULL,
    kind            TEXT NOT NULL,
    heading_path    TEXT NOT NULL,
    token_count     INTEGER NOT NULL,
    text            TEXT NOT NULL,
    embed_text      TEXT NOT NULL,
    embed_text_sha  TEXT NOT NULL
);
CREATE INDEX kb_chunks_document ON kb_chunks (document_id);

CREATE TABLE kb_version_chunks (
    version_id   TEXT NOT NULL REFERENCES kb_versions(id),
    chunk_id     TEXT NOT NULL REFERENCES kb_chunks(id),
    ordinal      INTEGER NOT NULL,
    spans        TEXT NOT NULL,
    element_ids  TEXT NOT NULL,
    pages        TEXT NOT NULL,
    PRIMARY KEY (version_id, ordinal)
);
CREATE INDEX kb_version_chunks_chunk ON kb_version_chunks (chunk_id);

-- Durable, content-addressed (track5 §11.3): (embedder_fp, embed_text_sha).
CREATE TABLE kb_embedding_cache (
    embedder_fp  TEXT NOT NULL,
    text_sha     TEXT NOT NULL,
    dim          INTEGER NOT NULL,
    vector       BLOB NOT NULL,
    PRIMARY KEY (embedder_fp, text_sha)
);

CREATE TABLE kb_ingest_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id  TEXT NOT NULL,
    key            TEXT NOT NULL,
    document_id    TEXT,
    version_id     TEXT,
    action         TEXT NOT NULL,
    stats          TEXT NOT NULL DEFAULT '{}',
    error          TEXT,
    at             TEXT NOT NULL
);
CREATE INDEX kb_ingest_log_key ON kb_ingest_log (collection_id, key);
