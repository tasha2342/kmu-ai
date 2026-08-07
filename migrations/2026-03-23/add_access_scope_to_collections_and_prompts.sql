-- 2026-03-23 Migration
-- collections, prompts: Keycloak 그룹/사용자별 접근 범위

ALTER TABLE collections
    ADD COLUMN IF NOT EXISTS access_scope VARCHAR(512) NULL;

CREATE INDEX IF NOT EXISTS collections_access_scope
    ON collections (access_scope);

ALTER TABLE prompts
    ADD COLUMN IF NOT EXISTS access_scope VARCHAR(512) NULL;

CREATE INDEX IF NOT EXISTS prompts_access_scope
    ON prompts (access_scope);
