-- Schema version 5: the concept graph (PLAN §10).
-- kb_graph_mentions holds the concepts each chunk of a version names, with the
-- edge's weight, committed with the version like its tree nodes: the graph of
-- a collection is the mentions of its active versions, so a commit, a delete
-- or a purge changes it with nothing else to keep in step.

CREATE TABLE kb_graph_mentions (
    version_id  TEXT NOT NULL REFERENCES kb_versions(id),
    chunk_id    TEXT NOT NULL,
    concept     TEXT NOT NULL,
    weight      DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (version_id, chunk_id, concept)
);
