-- 2026-07-27 Migration
-- 계명대 AI 챗봇 스키마 (기능정의서 v1.3.0 "3. ERD" 시트 기준)
--
-- app/main.py 기동 시 peewee의 create_tables(safe=True)가 이 테이블들을 자동 생성하므로
-- 신규 환경에서는 별도 실행이 필요 없습니다. 이 파일은 이미 떠 있는 DB에 수동 적용하거나
-- 스키마를 검토할 때 사용합니다.
--
-- chat_sessions / chat_messages는 ERD에 표로 정의돼 있지 않지만, ERD가 참조하는
-- session_id / message_id의 실체이자 대화 이력 로그(KAI-REQ-045)와 세션 기억(KAI-REQ-041)의
-- 저장소이므로 함께 정의합니다.
--
-- ERD 대비 변경점: 벡터 저장소를 Qdrant에서 PostgreSQL pgvector로 대체했습니다.
-- ERD의 faq_embedding_index 테이블 + qdrant.collection(kmu_faq_knowledge)은
-- faq_embeddings 한 테이블로 합쳐졌고, payload.* 필터 항목은 같은 테이블의 컬럼이 되었습니다.

CREATE EXTENSION IF NOT EXISTS vector;

-- FAQ 카테고리
CREATE TABLE IF NOT EXISTS faq_categories (
    id              UUID PRIMARY KEY,
    parent_id       UUID NULL,
    category_name   VARCHAR(100) NOT NULL,
    category_code   VARCHAR(50) NOT NULL UNIQUE,
    department_code VARCHAR(50) NULL,
    display_order   INTEGER NOT NULL DEFAULT 0,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS faq_categories_parent_id ON faq_categories (parent_id);
CREATE INDEX IF NOT EXISTS faq_categories_department_code ON faq_categories (department_code);
CREATE INDEX IF NOT EXISTS faq_categories_is_active ON faq_categories (is_active);

-- FAQ 항목
CREATE TABLE IF NOT EXISTS faq_items (
    id                    UUID PRIMARY KEY,
    category_id           UUID NOT NULL,
    question              TEXT NOT NULL,
    answer                TEXT NOT NULL,
    question_aliases_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    tags_json             JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_url            TEXT NULL,
    department_code       VARCHAR(50) NULL,
    visibility            VARCHAR(255) NOT NULL DEFAULT 'public',
    status                VARCHAR(255) NOT NULL DEFAULT 'draft',
    language              VARCHAR(255) NOT NULL DEFAULT 'ko',
    version               INTEGER NOT NULL DEFAULT 1,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS faq_items_category_id ON faq_items (category_id);
CREATE INDEX IF NOT EXISTS faq_items_department_code ON faq_items (department_code);
CREATE INDEX IF NOT EXISTS faq_items_visibility ON faq_items (visibility);
CREATE INDEX IF NOT EXISTS faq_items_status ON faq_items (status);
CREATE INDEX IF NOT EXISTS faq_items_language ON faq_items (language);
CREATE INDEX IF NOT EXISTS faq_items_created_at ON faq_items (created_at);
-- 태그 기반 관리자 통계(KAI-REQ-031)를 위한 GIN 인덱스
CREATE INDEX IF NOT EXISTS faq_items_tags_json ON faq_items USING GIN (tags_json);

-- FAQ 임베딩 (pgvector)
-- vector(1024)의 차원은 configs/config.yaml의 chatbot.embedding_dim과 반드시 같아야 하며,
-- 바꾸면 이 컬럼과 아래 HNSW 인덱스를 재생성하고 전량 재색인해야 합니다.
CREATE TABLE IF NOT EXISTS faq_embeddings (
    id                  UUID PRIMARY KEY,
    faq_id              UUID NOT NULL UNIQUE,
    embedding           VECTOR(1024) NULL,
    embedding_text      TEXT NOT NULL,
    embedding_text_hash VARCHAR(64) NOT NULL,
    embedding_model     VARCHAR(100) NOT NULL,
    embedding_version   VARCHAR(30) NULL,
    vector_status       VARCHAR(255) NOT NULL DEFAULT 'pending',
    category_code       VARCHAR(50) NULL,
    department_code     VARCHAR(50) NULL,
    language            VARCHAR(255) NOT NULL DEFAULT 'ko',
    visibility          VARCHAR(255) NOT NULL DEFAULT 'public',
    status              VARCHAR(255) NOT NULL DEFAULT 'draft',
    tags                JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_url          TEXT NULL,
    question            TEXT NOT NULL,
    version             INTEGER NOT NULL DEFAULT 1,
    indexed_at          TIMESTAMPTZ NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS faq_embeddings_embedding_text_hash ON faq_embeddings (embedding_text_hash);
CREATE INDEX IF NOT EXISTS faq_embeddings_vector_status ON faq_embeddings (vector_status);
CREATE INDEX IF NOT EXISTS faq_embeddings_category_code ON faq_embeddings (category_code);
CREATE INDEX IF NOT EXISTS faq_embeddings_department_code ON faq_embeddings (department_code);
CREATE INDEX IF NOT EXISTS faq_embeddings_language ON faq_embeddings (language);
CREATE INDEX IF NOT EXISTS faq_embeddings_visibility ON faq_embeddings (visibility);
CREATE INDEX IF NOT EXISTS faq_embeddings_status ON faq_embeddings (status);

-- 코사인 거리 기준 HNSW 인덱스 (app/utils/database.py가 기동 시 동일 문장을 실행합니다)
CREATE INDEX IF NOT EXISTS faq_embeddings_embedding_hnsw_idx
    ON faq_embeddings USING hnsw (embedding vector_cosine_ops);

-- 대화 세션
CREATE TABLE IF NOT EXISTS chat_sessions (
    id             UUID PRIMARY KEY,
    user_name      VARCHAR(255) NOT NULL,
    title          VARCHAR(255) NULL,
    language       VARCHAR(255) NOT NULL DEFAULT 'ko',
    status         VARCHAR(255) NOT NULL DEFAULT 'active',
    message_count  INTEGER NOT NULL DEFAULT 0,
    summary        TEXT NULL,
    profile        JSONB NULL,
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_sessions_user_name ON chat_sessions (user_name);
CREATE INDEX IF NOT EXISTS chat_sessions_status ON chat_sessions (status);
CREATE INDEX IF NOT EXISTS chat_sessions_last_active_at ON chat_sessions (last_active_at);
CREATE INDEX IF NOT EXISTS chat_sessions_created_at ON chat_sessions (created_at);

-- 대화 메시지
CREATE TABLE IF NOT EXISTS chat_messages (
    id              UUID PRIMARY KEY,
    session_id      UUID NOT NULL,
    role            VARCHAR(255) NOT NULL,
    content         TEXT NOT NULL,
    detected_intent VARCHAR(255) NULL,
    sources         JSONB NULL,
    attachments     JSONB NULL,
    model_name      VARCHAR(255) NULL,
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    is_answered     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_messages_session_id ON chat_messages (session_id);
CREATE INDEX IF NOT EXISTS chat_messages_role ON chat_messages (role);
CREATE INDEX IF NOT EXISTS chat_messages_detected_intent ON chat_messages (detected_intent);
CREATE INDEX IF NOT EXISTS chat_messages_is_answered ON chat_messages (is_answered);
CREATE INDEX IF NOT EXISTS chat_messages_created_at ON chat_messages (created_at);

-- 검색 로그 (KAI-REQ-043)
CREATE TABLE IF NOT EXISTS retrieval_logs (
    id                 UUID PRIMARY KEY,
    session_id         UUID NOT NULL,
    message_id         UUID NOT NULL,
    query_text         TEXT NOT NULL,
    detected_intent    VARCHAR(255) NULL,
    collection_name    VARCHAR(100) NULL,
    selected_source_id UUID NULL,
    result_count       INTEGER NOT NULL DEFAULT 0,
    latency_ms         INTEGER NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS retrieval_logs_session_id ON retrieval_logs (session_id);
CREATE INDEX IF NOT EXISTS retrieval_logs_message_id ON retrieval_logs (message_id);
CREATE INDEX IF NOT EXISTS retrieval_logs_detected_intent ON retrieval_logs (detected_intent);
CREATE INDEX IF NOT EXISTS retrieval_logs_selected_source_id ON retrieval_logs (selected_source_id);
CREATE INDEX IF NOT EXISTS retrieval_logs_created_at ON retrieval_logs (created_at);

-- 미응답 질문 (KAI-REQ-031/040)
CREATE TABLE IF NOT EXISTS unanswered_questions (
    id            UUID PRIMARY KEY,
    session_id    UUID NOT NULL,
    message_id    UUID NOT NULL,
    question_text TEXT NOT NULL,
    reason        VARCHAR(255) NOT NULL,
    review_status VARCHAR(255) NOT NULL DEFAULT 'pending',
    reviewed_by   VARCHAR(100) NULL,
    reviewed_at   TIMESTAMPTZ NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS unanswered_questions_session_id ON unanswered_questions (session_id);
CREATE INDEX IF NOT EXISTS unanswered_questions_message_id ON unanswered_questions (message_id);
CREATE INDEX IF NOT EXISTS unanswered_questions_reason ON unanswered_questions (reason);
CREATE INDEX IF NOT EXISTS unanswered_questions_review_status ON unanswered_questions (review_status);
CREATE INDEX IF NOT EXISTS unanswered_questions_created_at ON unanswered_questions (created_at);

-- 사용자 피드백 (KAI-REQ-033)
CREATE TABLE IF NOT EXISTS user_feedbacks (
    id            UUID PRIMARY KEY,
    session_id    UUID NOT NULL,
    message_id    UUID NOT NULL,
    rating        INTEGER NOT NULL,
    feedback_text TEXT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS user_feedbacks_session_id ON user_feedbacks (session_id);
CREATE INDEX IF NOT EXISTS user_feedbacks_rating ON user_feedbacks (rating);
CREATE INDEX IF NOT EXISTS user_feedbacks_created_at ON user_feedbacks (created_at);
-- 메시지당 피드백 1건 (재등록은 갱신으로 처리)
CREATE UNIQUE INDEX IF NOT EXISTS user_feedbacks_message_id ON user_feedbacks (message_id);

-- 수집 작업 (KAI-REQ-014)
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id            UUID PRIMARY KEY,
    source_type   VARCHAR(255) NOT NULL,
    status        VARCHAR(255) NOT NULL DEFAULT 'pending',
    total_count   INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failed_count  INTEGER NOT NULL DEFAULT 0,
    error_message VARCHAR(1024) NULL,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at      TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS ingestion_jobs_source_type ON ingestion_jobs (source_type);
CREATE INDEX IF NOT EXISTS ingestion_jobs_status ON ingestion_jobs (status);
CREATE INDEX IF NOT EXISTS ingestion_jobs_started_at ON ingestion_jobs (started_at);

-- 수집 작업 항목
CREATE TABLE IF NOT EXISTS ingestion_job_items (
    id            UUID PRIMARY KEY,
    job_id        UUID NOT NULL,
    source_table  VARCHAR(100) NOT NULL,
    source_id     UUID NOT NULL,
    status        VARCHAR(255) NOT NULL DEFAULT 'pending',
    error_message TEXT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ingestion_job_items_job_id ON ingestion_job_items (job_id);
CREATE INDEX IF NOT EXISTS ingestion_job_items_source_id ON ingestion_job_items (source_id);
CREATE INDEX IF NOT EXISTS ingestion_job_items_status ON ingestion_job_items (status);
CREATE INDEX IF NOT EXISTS ingestion_job_items_created_at ON ingestion_job_items (created_at);
