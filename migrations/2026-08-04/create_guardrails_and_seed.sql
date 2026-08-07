-- 2026-08-04 Migration
-- 가드레일(입·출력 필터) 테이블 + 기본 시드
-- 마스킹 규칙 기본 시드 (테이블은 2026-07-28 마이그레이션으로 이미 생성된 경우)

-- ===== 가드레일 =====

CREATE TABLE IF NOT EXISTS guardrails (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    direction VARCHAR(20) NOT NULL,
    match_type VARCHAR(20) NOT NULL,
    pattern TEXT NOT NULL,
    action VARCHAR(20) NOT NULL,
    response_message TEXT,
    replacement VARCHAR(64),
    description TEXT,
    priority INT NOT NULL DEFAULT 100,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS guardrails_name ON guardrails (name);
CREATE INDEX IF NOT EXISTS guardrails_direction ON guardrails (direction);
CREATE INDEX IF NOT EXISTS guardrails_match_type ON guardrails (match_type);
CREATE INDEX IF NOT EXISTS guardrails_action ON guardrails (action);
CREATE INDEX IF NOT EXISTS guardrails_priority ON guardrails (priority);
CREATE INDEX IF NOT EXISTS guardrails_is_active ON guardrails (is_active);
CREATE INDEX IF NOT EXISTS guardrails_created_at ON guardrails (created_at);

-- 입력: 프롬프트 인젝션·탈옥 시도 차단
INSERT INTO guardrails (
    id, name, direction, match_type, pattern, action, response_message, description, priority, is_active
)
SELECT
    'b1000000-0000-4000-8000-000000000001'::uuid,
    '프롬프트 인젝션',
    'input',
    'regex',
    '(?i)(ignore (all )?previous|system prompt|you are now|jailbreak|DAN mode|forget (your )?instructions|역할.?무시|이전.?지시.?무시|시스템.?프롬프트)',
    'block',
    '학사·취업 안내 목적의 질문만 도와드릴 수 있습니다. 다른 요청은 처리할 수 없습니다.',
    '모델 지시를 덮어쓰려는 입력을 차단합니다.',
    10,
    true
WHERE NOT EXISTS (
    SELECT 1 FROM guardrails WHERE id = 'b1000000-0000-4000-8000-000000000001'::uuid
);

-- 입력: 명백한 욕설·비속어 (정서 표현 "죽겠다" 등은 제외)
INSERT INTO guardrails (
    id, name, direction, match_type, pattern, action, response_message, description, priority, is_active
)
SELECT
    'b1000000-0000-4000-8000-000000000002'::uuid,
    '욕설·비속어',
    'input',
    'regex',
    '(?i)(시발|씨발|ㅅㅂ|ㅆㅂ|병신|개새|좆|지랄|fuck|shit|bitch)',
    'block',
    '부적절한 표현이 포함되어 있어 답변할 수 없습니다. 학사·취업 관련 질문을 다시 입력해 주세요.',
    '명백한 욕설·비속어 입력을 차단합니다. (KAI-REQ-038)',
    20,
    true
WHERE NOT EXISTS (
    SELECT 1 FROM guardrails WHERE id = 'b1000000-0000-4000-8000-000000000002'::uuid
);

-- 입력: 타인 개인정보 조회 시도
INSERT INTO guardrails (
    id, name, direction, match_type, pattern, action, response_message, description, priority, is_active
)
SELECT
    'b1000000-0000-4000-8000-000000000003'::uuid,
    '타인 개인정보 조회',
    'input',
    'regex',
    '(?i)(다른\s*사람|타인|남의).{0,24}(학번|주민|전화|성적|개인정보)',
    'block',
    '타인의 개인정보는 조회·제공할 수 없습니다. 본인 관련 학사 문의는 학사지원팀에 문의해 주세요.',
    '타인 개인정보 조회·열람 요청을 차단합니다. (KAI-REQ-035)',
    30,
    true
WHERE NOT EXISTS (
    SELECT 1 FROM guardrails WHERE id = 'b1000000-0000-4000-8000-000000000003'::uuid
);

-- 출력: 주민등록번호 패턴 치환 (2차 방어)
INSERT INTO guardrails (
    id, name, direction, match_type, pattern, action, replacement, description, priority, is_active
)
SELECT
    'b1000000-0000-4000-8000-000000000004'::uuid,
    '주민등록번호 출력',
    'output',
    'regex',
    '\d{6}-?\d{7}',
    'replace',
    '******-*******',
    '응답에 주민등록번호가 포함되면 치환합니다.',
    40,
    true
WHERE NOT EXISTS (
    SELECT 1 FROM guardrails WHERE id = 'b1000000-0000-4000-8000-000000000004'::uuid
);

-- 출력: 전화번호 패턴 치환 (2차 방어)
INSERT INTO guardrails (
    id, name, direction, match_type, pattern, action, replacement, description, priority, is_active
)
SELECT
    'b1000000-0000-4000-8000-000000000005'::uuid,
    '전화번호 출력',
    'output',
    'regex',
    '01[0-9]-?\d{3,4}-?\d{4}',
    'replace',
    '010-****-****',
    '응답에 휴대폰 번호가 포함되면 치환합니다.',
    50,
    true
WHERE NOT EXISTS (
    SELECT 1 FROM guardrails WHERE id = 'b1000000-0000-4000-8000-000000000005'::uuid
);

-- ===== 마스킹 규칙 시드 (비어 있을 때만) =====

INSERT INTO masking_rules (
    id, name, target_field, regex_pattern, masking_method, replacement, description, is_active, created_at, updated_at
)
SELECT
    'a1000000-0000-4000-8000-000000000001'::uuid,
    '학번',
    'student_id',
    '(?<!\d)\d{8}(?!\d)',
    'partial',
    '****',
    '8자리 학번 부분 마스킹',
    true,
    NOW(),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM masking_rules WHERE id = 'a1000000-0000-4000-8000-000000000001'::uuid
);

INSERT INTO masking_rules (
    id, name, target_field, regex_pattern, masking_method, replacement, description, is_active, created_at, updated_at
)
SELECT
    'a1000000-0000-4000-8000-000000000002'::uuid,
    '전화번호',
    'phone',
    '01[0-9]-?\d{3,4}-?\d{4}',
    'middle',
    '****',
    '휴대폰 번호 중간 자리 마스킹',
    true,
    NOW(),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM masking_rules WHERE id = 'a1000000-0000-4000-8000-000000000002'::uuid
);

INSERT INTO masking_rules (
    id, name, target_field, regex_pattern, masking_method, replacement, description, is_active, created_at, updated_at
)
SELECT
    'a1000000-0000-4000-8000-000000000003'::uuid,
    '이메일',
    'email',
    '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
    'partial',
    '****',
    '이메일 로컬파트 부분 마스킹',
    true,
    NOW(),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM masking_rules WHERE id = 'a1000000-0000-4000-8000-000000000003'::uuid
);

INSERT INTO masking_rules (
    id, name, target_field, regex_pattern, masking_method, replacement, description, is_active, created_at, updated_at
)
SELECT
    'a1000000-0000-4000-8000-000000000004'::uuid,
    '주민등록번호',
    'resident_id',
    '\d{6}-?\d{7}',
    'full',
    '******-*******',
    '주민등록번호 전체 마스킹',
    true,
    NOW(),
    NOW()
WHERE NOT EXISTS (
    SELECT 1 FROM masking_rules WHERE id = 'a1000000-0000-4000-8000-000000000004'::uuid
);
