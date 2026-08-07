"""개인정보 마스킹(비식별화) 유틸리티

활성 `masking_rules`를 불러와 텍스트에 정규식 기반 마스킹을 적용합니다.
챗봇 메시지·검색 로그 저장 및 응답 출력 직전에 사용합니다.
"""

from __future__ import annotations

import re

from logging import getLogger
from typing import Optional, Sequence

from app.models.enum import MaskingMethod
from app.utils.database import DatabaseManager
import app.models.database as db_models
import app.models.db_item as db_items


logger = getLogger("pii_mask")


async def load_active_rules(db_manager: DatabaseManager) -> list[db_items.MaskingRule]:
    """활성 마스킹 규칙을 조회합니다."""

    query = (db_models.MaskingRule
             .select()
             .where(db_models.MaskingRule.is_active == True)
             .order_by(db_models.MaskingRule.created_at.asc()))
    return await db_manager.select_items(query)


def _mask_match(matched: str, method: MaskingMethod | str, replacement: str) -> str:
    """단일 매칭 문자열에 마스킹 방식을 적용합니다."""

    method_value = method.value if hasattr(method, "value") else str(method)
    replacement = replacement or "****"

    if method_value == MaskingMethod.FULL.value:
        return replacement

    if not matched:
        return matched

    length = len(matched)

    if method_value == MaskingMethod.MIDDLE.value:
        # 앞 3·뒤 4 정도를 남기고 중간을 치환 (전화 번호 패턴에 맞춤)
        if length <= 4:
            return replacement
        keep_head = min(3, length // 3)
        keep_tail = min(4, length // 3)
        if keep_head + keep_tail >= length:
            return matched[0] + replacement + matched[-1]
        return matched[:keep_head] + replacement + matched[-keep_tail:]

    # partial: 앞 1~2, 뒤 1~2 보존
    if length <= 2:
        return replacement
    keep_head = 1 if length < 6 else 2
    keep_tail = 1 if length < 8 else 2
    if "@" in matched:
        # 이메일은 로컬파트만 부분 마스킹
        local, _, domain = matched.partition("@")
        if len(local) <= 1:
            masked_local = replacement
        else:
            masked_local = local[0] + replacement
        return f"{masked_local}@{domain}"
    if keep_head + keep_tail >= length:
        return matched[0] + replacement + matched[-1]
    return matched[:keep_head] + replacement + matched[-keep_tail:]


def apply_masking(
    text: Optional[str],
    rules: Sequence[db_items.MaskingRule | dict],
) -> str:
    """텍스트에 활성 규칙을 순차 적용합니다.

    Args:
        text: 원문
        rules: 마스킹 규칙 목록

    Returns:
        마스킹된 문자열 (text가 None/빈 문자열이면 그대로)
    """

    if text is None:
        return text
    if not text or not rules:
        return text

    result = text
    for rule in rules:
        if isinstance(rule, dict):
            pattern = rule.get("regex_pattern") or ""
            method = rule.get("masking_method") or MaskingMethod.FULL
            replacement = rule.get("replacement") or "****"
            name = rule.get("name") or "?"
        else:
            pattern = rule.regex_pattern or ""
            method = rule.masking_method
            replacement = rule.replacement or "****"
            name = rule.name

        if not pattern:
            continue
        try:
            compiled = re.compile(pattern)
        except re.error:
            logger.warning(f"잘못된 마스킹 정규식입니다. 규칙을 건너뜁니다. (name={name}, pattern={pattern})")
            continue

        def _replacer(match: re.Match, _method=method, _replacement=replacement) -> str:
            return _mask_match(match.group(0), _method, _replacement)

        try:
            result = compiled.sub(_replacer, result)
        except Exception:
            logger.exception(f"마스킹 적용 중 오류 (name={name})")
            continue

    return result


async def mask_text(db_manager: DatabaseManager, text: Optional[str]) -> str:
    """활성 규칙을 DB에서 로드해 텍스트에 적용합니다."""

    if not text:
        return text
    rules = await load_active_rules(db_manager)
    return apply_masking(text, rules)


def validate_regex(pattern: str) -> None:
    """정규식이 컴파일 가능한지 검증합니다. 실패 시 ValueError."""

    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"유효하지 않은 정규표현식입니다. ({exc})") from exc
