-- 2026-03-12 Migration
-- models 테이블에 description, cost 컬럼 추가
-- model_usages 테이블: 토큰 개별 컬럼 → usage JSON 컬럼으로 통합

-- models 테이블
ALTER TABLE models
    ADD COLUMN IF NOT EXISTS description VARCHAR(1024) NULL;

ALTER TABLE models
    ADD COLUMN IF NOT EXISTS cost JSONB NULL;

-- model_usages 테이블: usage JSON 컬럼 추가 + 기존 데이터 마이그레이션 + 기존 컬럼 제거
ALTER TABLE model_usages
    ADD COLUMN IF NOT EXISTS usage JSONB NULL;

UPDATE model_usages
    SET usage = jsonb_build_object(
        'input_tokens', input_tokens,
        'cached_input_tokens', 0,
        'output_tokens', output_tokens
    )
    WHERE usage IS NULL AND model_type = 'text_generation';

UPDATE model_usages
    SET usage = jsonb_build_object(
        'single_tokens', total_tokens
    )
    WHERE usage IS NULL AND model_type = 'embedding';

UPDATE model_usages
    SET usage = '{}'::jsonb
    WHERE usage IS NULL AND model_type = 'rerank';

ALTER TABLE model_usages
    DROP COLUMN IF EXISTS input_tokens,
    DROP COLUMN IF EXISTS cached_input_tokens,
    DROP COLUMN IF EXISTS output_tokens,
    DROP COLUMN IF EXISTS total_tokens;
