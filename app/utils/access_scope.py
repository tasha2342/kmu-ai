import operator
import re

from functools import reduce

from typing import Optional

from app.config import config
from app.models.auth import TokenUserInfo


_ACCESS_SCOPE_PATTERN = re.compile(r"^(group|user):(.+)$")


def normalize_access_scope(value: Optional[str]) -> Optional[str]:
    """빈 문자열은 None으로 통일합니다.
    
    Args:
        value (Optional[str]): 접근 가능 범위 문자열

    Returns:
        Optional[str]: 정규화된 접근 가능 범위 문자열
    """
    
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    return value.strip()

def validate_access_scope(value: Optional[str]) -> Optional[str]:
    """저장 가능한 access_scope 값이면 정규화된 문자열을, 전체 공개면 None을 반환합니다.

    Args:
        value (Optional[str]): 접근 가능 범위 문자열

    Returns:
        Optional[str]: 정규화된 접근 가능 범위 문자열
    """
    
    normalized = normalize_access_scope(value)
    if normalized is None:
        return None
    if not _ACCESS_SCOPE_PATTERN.match(normalized):
        raise ValueError(
            "access_scope는 'group:{group_name}' 또는 'user:{user_name}' 형식이어야 하며, "
            "콜론 뒤는 비울 수 없습니다."
        )
    return normalized

def user_has_admin_access(user: TokenUserInfo) -> bool:
    """config.auth.admin_roles 중 하나라도 realm 역할에 있으면 True를 반환합니다.
    
    Args:
        user (TokenUserInfo): 사용자 정보

    Returns:
        bool: 관리자 여부
    """
    
    admin_roles = config.auth.admin_roles or []
    user_roles = set(user.roles or [])
    return any(role in user_roles for role in admin_roles)

def can_access_scope(user: TokenUserInfo, access_scope: Optional[str]) -> bool:
    """관리자가 아닐 때 접근 가능 범위를 확인합니다.
    
    Args:
        user (TokenUserInfo): 사용자 정보
        access_scope (Optional[str]): 접근 가능 범위 문자열

    Returns:
        bool: 접근 가능 여부
    """
    
    if user_has_admin_access(user):
        return True
    
    scope = normalize_access_scope(access_scope)
    if scope is None:
        return True
    
    match = _ACCESS_SCOPE_PATTERN.match(scope)
    if not match:
        return False
    
    kind, rest = match.group(1), match.group(2)
    if kind == "group":
        groups = user.groups or []
        return rest in groups
    if kind == "user":
        if user.username is None:
            return False
        return user.username == rest
    return False

def peewee_access_scope_filter(access_scope_field, user: TokenUserInfo):
    """관리자가 아닐 때 DB 쿼리에 붙일 OR 조건(NULL·허용 group·user)을 반환합니다.
    
    Args:
        access_scope_field (Field): 접근 가능 범위 필드
        user (TokenUserInfo): 사용자 정보

    Returns:
        Optional[Expression]: OR 조건
    """
    
    if user_has_admin_access(user):
        return None
    
    parts = [access_scope_field.is_null(True)]
    for g in user.groups or []:
        parts.append(access_scope_field == f"group:{g}")
    
    if user.username:
        parts.append(access_scope_field == f"user:{user.username}")
    return reduce(operator.or_, parts)
