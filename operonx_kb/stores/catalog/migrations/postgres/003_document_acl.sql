-- Schema version 3: a document's ACL (the principals that may read it).
-- It is copied into every index entry (kb_acl) and re-checked at hydration,
-- so KBFilter(acl_any=...) is answered by the index and the catalog alike.

ALTER TABLE kb_documents ADD COLUMN acl TEXT NOT NULL DEFAULT '[]';
