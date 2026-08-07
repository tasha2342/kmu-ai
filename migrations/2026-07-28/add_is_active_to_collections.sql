-- 2026-07-28 Migration
-- collections: RAG 관리 화면용 활성(검색 노출) 토글

ALTER TABLE collections
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;

CREATE INDEX IF NOT EXISTS collections_is_active
    ON collections (is_active);
