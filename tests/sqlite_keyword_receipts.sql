-- Offline fixture / proposed native migration. NOT applied to a running DB.
-- No FK to knowledges: completion/deletion ownership must survive doc deletion.
CREATE TABLE sync_keyword_receipts (
 operation_id TEXT PRIMARY KEY,
 tenant_id INTEGER NOT NULL,
 knowledge_base_id TEXT NOT NULL,
 knowledge_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('queued','running','failed','done')),
 task_id TEXT,
 generation INTEGER,
 payload TEXT NOT NULL CHECK(json_valid(payload))
);
CREATE INDEX sync_keyword_receipts_owner ON sync_keyword_receipts(tenant_id,knowledge_base_id,knowledge_id);
CREATE VIRTUAL TABLE lite_embeddings_fts_vocab USING fts5vocab(lite_embeddings_fts,'instance');
