-- 2026-07-28 Migration
-- 개인정보 마스킹 규칙 테이블 + 기본 시드

CREATE TABLE IF NOT EXISTS masking_rules (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    target_field VARCHAR(50) NOT NULL,
    regex_pattern TEXT NOT NULL,
    masking_method VARCHAR(50) NOT NULL,
    replacement VARCHAR(64) NOT NULL DEFAULT '****',
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS masking_rules_name ON masking_rules (name);
CREATE INDEX IF NOT EXISTS masking_rules_target_field ON masking_rules (target_field);
CREATE INDEX IF NOT EXISTS masking_rules_masking_method ON masking_rules (masking_method);
CREATE INDEX IF NOT EXISTS masking_rules_is_active ON masking_rules (is_active);
CREATE INDEX IF NOT EXISTS masking_rules_created_at ON masking_rules (created_at);

-- 목업 기본 규칙 (이미 있으면면 건너뜀)
INSERT INTO masking_rules (
    id, name, target_field, regex_pattern, masking_method, replacement, description, is_active
)
SELECT
    'a1000000-0000-4000-8000-000000000001'::uuid,
    '학번',
    'student_id',
    '^\d{8}$',
    'partial',
    '****',
    '학번 8자리 부분 마스킹',
    true
WHERE NOT EXISTS (
    SELECT 1 FROM masking_rules WHERE id = 'a1000000-0000-4000-8000-000000000001'::uuid
);

INSERT INTO masking_rules (
    id, name, target_field, regex_pattern, masking_method, replacement, description, is_active
)
SELECT
    'a1000000-0000-4000-8000-000000000002'::uuid,
    '전화번호',
    'phone',
    '01[0-9]-?\d{3,4}-?\d{4}',
    'middle',
    '****',
    '휴대폰 번호 중간 자리 마스킹',
    true
WHERE NOT EXISTS (
    SELECT 1 FROM masking_rules WHERE id = 'a1000000-0000-4000-8000-000000000002'::uuid
);

INSERT INTO masking_rules (
    id, name, target_field, regex_pattern, masking_method, replacement, description, is_active
)
SELECT
    'a1000000-0000-4000-8000-000000000003'::uuid,
    '이메일',
    'email',
    '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
    'partial',
    '****',
    '이메일 로컬파트 부분 마스킹',
    true
WHERE NOT EXISTS (
    SELECT 1 FROM masking_rules WHERE id = 'a1000000-0000-4000-8000-000000000003'::uuid
);

INSERT INTO masking_rules (
    id, name, target_field, regex_pattern, masking_method, replacement, description, is_active
)
SELECT
    'a1000000-0000-4000-8000-000000000004'::uuid,
    '주민등록번호',
    'resident_id',
    '\d{6}-?\d{7}',
    'full',
    '******-*******',
    '주민등록번호 전체 마스킹',
    true
WHERE NOT EXISTS (
    SELECT 1 FROM masking_rules WHERE id = 'a1000000-0000-4000-8000-000000000004'::uuid
);
