-- Schema version 2: the ledger of derived-index writes.
-- A vector store cannot list what it holds, so the catalog records every key
-- the KB writes to one (the record-manager idea, track5 §2). A row is added
-- before the vector is upserted and removed after it is deleted, so the ledger
-- always covers the index: GC deletes what the ledger holds and the active
-- versions do not, and verify compares the ledger with the active chunks.

CREATE TABLE kb_index_entries (
    store          TEXT NOT NULL,
    collection     TEXT NOT NULL,
    chunk_id       TEXT NOT NULL,
    vector_id      INTEGER NOT NULL,
    document_id    TEXT NOT NULL,
    collection_id  TEXT NOT NULL,
    PRIMARY KEY (store, collection, chunk_id)
);
CREATE INDEX kb_index_entries_vector ON kb_index_entries (store, collection, vector_id);
CREATE INDEX kb_index_entries_document ON kb_index_entries (document_id);
