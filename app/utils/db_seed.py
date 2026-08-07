"""기본 마스킹·가드레일 시드

서버 기동 시 테이블이 비어 있으면 기본 규칙을 넣습니다.
`migrations/2026-08-04/create_guardrails_and_seed.sql`과 동일한 내용입니다.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import app.models.database as db_models
import app.utils.common as util
from app.models.enum import (
    GuardrailAction,
    GuardrailDirection,
    GuardrailMatchType,
    MaskingMethod,
    MaskingTargetField,
)
from app.utils.logger import get_logger


logger = get_logger("db_seed")

MASKING_RULE_SEEDS: tuple[dict, ...] = (
    {
        "id": UUID("a1000000-0000-4000-8000-000000000001"),
        "name": "학번",
        "target_field": MaskingTargetField.STUDENT_ID,
        "regex_pattern": r"(?<!\d)\d{8}(?!\d)",
        "masking_method": MaskingMethod.PARTIAL,
        "replacement": "****",
        "description": "8자리 학번 부분 마스킹",
    },
    {
        "id": UUID("a1000000-0000-4000-8000-000000000002"),
        "name": "전화번호",
        "target_field": MaskingTargetField.PHONE,
        "regex_pattern": r"01[0-9]-?\d{3,4}-?\d{4}",
        "masking_method": MaskingMethod.MIDDLE,
        "replacement": "****",
        "description": "휴대폰 번호 중간 자리 마스킹",
    },
    {
        "id": UUID("a1000000-0000-4000-8000-000000000003"),
        "name": "이메일",
        "target_field": MaskingTargetField.EMAIL,
        "regex_pattern": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        "masking_method": MaskingMethod.PARTIAL,
        "replacement": "****",
        "description": "이메일 로컬파트 부분 마스킹",
    },
    {
        "id": UUID("a1000000-0000-4000-8000-000000000004"),
        "name": "주민등록번호",
        "target_field": MaskingTargetField.RESIDENT_ID,
        "regex_pattern": r"\d{6}-?\d{7}",
        "masking_method": MaskingMethod.FULL,
        "replacement": "******-*******",
        "description": "주민등록번호 전체 마스킹",
    },
)

GUARDRAIL_SEEDS: tuple[dict, ...] = (
    {
        "id": UUID("b1000000-0000-4000-8000-000000000001"),
        "name": "프롬프트 인젝션",
        "direction": GuardrailDirection.INPUT,
        "match_type": GuardrailMatchType.REGEX,
        "pattern": (
            r"(?i)(ignore (all )?previous|system prompt|you are now|jailbreak|DAN mode|"
            r"forget (your )?instructions|역할.?무시|이전.?지시.?무시|시스템.?프롬프트)"
        ),
        "action": GuardrailAction.BLOCK,
        "response_message": "학사·취업 안내 목적의 질문만 도와드릴 수 있습니다. 다른 요청은 처리할 수 없습니다.",
        "replacement": None,
        "description": "모델 지시를 덮어쓰려는 입력을 차단합니다.",
        "priority": 10,
    },
    {
        "id": UUID("b1000000-0000-4000-8000-000000000002"),
        "name": "욕설·비속어",
        "direction": GuardrailDirection.INPUT,
        "match_type": GuardrailMatchType.REGEX,
        "pattern": r"(?i)(시발|씨발|ㅅㅂ|ㅆㅂ|병신|개새|좆|지랄|fuck|shit|bitch)",
        "action": GuardrailAction.BLOCK,
        "response_message": (
            "부적절한 표현이 포함되어 있어 답변할 수 없습니다. "
            "학사·취업 관련 질문을 다시 입력해 주세요."
        ),
        "replacement": None,
        "description": "명백한 욕설·비속어 입력을 차단합니다. (KAI-REQ-038)",
        "priority": 20,
    },
    {
        "id": UUID("b1000000-0000-4000-8000-000000000003"),
        "name": "타인 개인정보 조회",
        "direction": GuardrailDirection.INPUT,
        "match_type": GuardrailMatchType.REGEX,
        "pattern": r"(?i)(다른\s*사람|타인|남의).{0,24}(학번|주민|전화|성적|개인정보)",
        "action": GuardrailAction.BLOCK,
        "response_message": (
            "타인의 개인정보는 조회·제공할 수 없습니다. "
            "본인 관련 학사 문의는 학사지원팀에 문의해 주세요."
        ),
        "replacement": None,
        "description": "타인 개인정보 조회·열람 요청을 차단합니다. (KAI-REQ-035)",
        "priority": 30,
    },
    {
        "id": UUID("b1000000-0000-4000-8000-000000000004"),
        "name": "주민등록번호 출력",
        "direction": GuardrailDirection.OUTPUT,
        "match_type": GuardrailMatchType.REGEX,
        "pattern": r"\d{6}-?\d{7}",
        "action": GuardrailAction.REPLACE,
        "response_message": None,
        "replacement": "******-*******",
        "description": "응답에 주민등록번호가 포함되면 치환합니다.",
        "priority": 40,
    },
    {
        "id": UUID("b1000000-0000-4000-8000-000000000005"),
        "name": "전화번호 출력",
        "direction": GuardrailDirection.OUTPUT,
        "match_type": GuardrailMatchType.REGEX,
        "pattern": r"01[0-9]-?\d{3,4}-?\d{4}",
        "action": GuardrailAction.REPLACE,
        "response_message": None,
        "replacement": "010-****-****",
        "description": "응답에 휴대폰 번호가 포함되면 치환합니다.",
        "priority": 50,
    },
)


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _seed_default_masking_and_guardrails_sync() -> None:
    """마스킹·가드레일 테이블이 비어 있으면 기본 규칙을 넣습니다."""

    now = util.get_now()

    if db_models.MaskingRule.select().count() == 0:
        rows = [{
            **seed,
            "target_field": _enum_value(seed["target_field"]),
            "masking_method": _enum_value(seed["masking_method"]),
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        } for seed in MASKING_RULE_SEEDS]
        db_models.MaskingRule.insert_many(rows).execute()
        logger.info(f"기본 마스킹 규칙 {len(rows)}건을 시드했습니다.")

    if db_models.Guardrail.select().count() == 0:
        rows = [{
            **seed,
            "direction": _enum_value(seed["direction"]),
            "match_type": _enum_value(seed["match_type"]),
            "action": _enum_value(seed["action"]),
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        } for seed in GUARDRAIL_SEEDS]
        db_models.Guardrail.insert_many(rows).execute()
        logger.info(f"기본 가드레일 규칙 {len(rows)}건을 시드했습니다.")


async def seed_default_masking_and_guardrails() -> None:
    """기본 마스킹·가드레일 시드를 비동기로 실행합니다."""

    await asyncio.to_thread(_seed_default_masking_and_guardrails_sync)
