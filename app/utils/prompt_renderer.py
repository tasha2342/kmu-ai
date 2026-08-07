import re

from datetime import datetime

from typing import Optional

from app.models.auth import TokenUserInfo


def render(
    content: str,
    user_info: Optional[TokenUserInfo] = None,
    variables: Optional[dict[str, str]] = None
) -> str:
    """프롬프트 내용의 변수를 치환합니다.
    
    지원하는 변수:
    - {{now:FORMAT}} - 현재 날짜시간 (기본: yyyy-MM-dd HH:mm:ss)
    - {{user_id}} - 사용자 ID
    - {{user_name}} - 사용자 이름
    - 기타 커스텀 변수 (variables 파라미터로 전달)
    
    Args:
        content (str): 원본 프롬프트 내용
        user_info (Optional[TokenUserInfo]): 사용자 정보
        variables (Optional[dict[str, str]]): 커스텀 변수
        
    Returns:
        str: 변수가 치환된 프롬프트 내용
    """
    
    if not content:
        return content
    
    rendered = content
    now = datetime.now()
    
    # 현재 날짜와 시간 - {{now:FORMAT}} or {{now}}
    now_pattern = r'\{\{now(?::([^}]+))?\}\}'
    for match in re.finditer(now_pattern, rendered):
        format_str = match.group(1) if match.group(1) else "yyyy-MM-dd HH:mm:ss"
        python_format = _convert_format_to_python(format_str)
        datetime_value = now.strftime(python_format)
        rendered = rendered.replace(match.group(0), datetime_value)
    
    # 사용자 정보 변수 치환
    if user_info:
        rendered = rendered.replace("{{user_id}}", user_info.username or "")
        rendered = rendered.replace("{{user_name}}", f"{user_info.family_name}{user_info.given_name}".strip() or "")
    
    # 커스텀 변수 치환
    if variables:
        for key, value in variables.items():
            rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    
    return rendered

def _convert_format_to_python(format_str: str) -> str:
    """C# 스타일 날짜 포맷을 Python strftime 포맷으로 변환합니다.
    
    Args:
        format_str (str): C# 스타일 포맷 (예: yyyy-MM-dd HH:mm:ss)
        
    Returns:
        str: Python strftime 포맷 (예: %Y-%m-%d %H:%M:%S)
    """
    
    # 긴 패턴부터 먼저 처리 (겹치는 패턴 방지)
    replacements = [
        (r'yyyy', '%Y'),   # 4자리 연도
        (r'yy', '%y'),     # 2자리 연도
        (r'y', '%Y'),      # 연도
        (r'MMMM', '%B'),   # 전체 월 이름
        (r'MMM', '%b'),    # 축약 월 이름
        (r'MM', '%m'),     # 2자리 월
        (r'M', '%m'),      # 1-2자리 월
        (r'dddd', '%A'),   # 전체 요일 이름
        (r'ddd', '%a'),    # 축약 요일 이름
        (r'dd', '%d'),     # 2자리 일
        (r'd', '%d'),      # 1-2자리 일
        (r'HH', '%H'),     # 24시간 형식 2자리
        (r'H', '%H'),      # 24시간 형식 1-2자리
        (r'hh', '%I'),     # 12시간 형식 2자리
        (r'h', '%I'),      # 12시간 형식 1-2자리
        (r'mm', '%M'),     # 2자리 분
        (r'm', '%M'),      # 1-2자리 분
        (r'ss', '%S'),     # 2자리 초
        (r's', '%S'),      # 1-2자리 초
        (r'fff', '%f'),    # 마이크로초
        (r'ff', '%f'),     # 마이크로초
        (r'f', '%f'),      # 마이크로초
        (r'tt', '%p'),     # AM/PM
        (r't', '%p'),      # AM/PM 첫 문자
    ]
    
    result = format_str
    for pattern, replacement in replacements:
        # 패턴이 % 뒤에 오지 않을 때만 매칭
        result = re.sub(f'(?<!%){pattern}(?![a-zA-Z])', replacement, result)
    
    return result
