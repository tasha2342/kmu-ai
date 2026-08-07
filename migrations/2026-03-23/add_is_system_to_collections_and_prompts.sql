-- 2026-03-23 Migration
-- collections, prompts: 시스템 전용 항목 (UI 기본 비표시)

ALTER TABLE collections
    ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS collections_is_system
    ON collections (is_system);

ALTER TABLE prompts
    ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS prompts_is_system
    ON prompts (is_system);
