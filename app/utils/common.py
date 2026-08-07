import pytz
import random
import string
import os
import base64

from datetime import datetime, date, time

from zoneinfo import ZoneInfo

from typing import Awaitable, Optional, Any

from fastapi import Request

from app.config import env


def get_timezone(tz: str) -> pytz.BaseTzInfo:
    """입력받은 시간대 문자열로 timezone 객체를 생성합니다.
    
    Args:
        tz (str): 시간대 문자열 (예: 'Asia/Seoul')
    
    Returns:
        pytz.BaseTzInfo: 입력받은 시간대의 timezone 객체
    """
    
    return pytz.timezone(tz)

def get_now(tz: Optional[str] = None) -> datetime:
    """입력받은 시간대의 현재 시간을 가져옵니다.
    
    Args:
        tz (str): 시간대 문자열 (Default: Environment 설정)

    Returns:
        datetime: 입력받은 시간대의 현재 시간
    """
    
    return datetime.now(get_timezone(tz or env.TZ))

def generate_id(k=29) -> str:
    """랜덤한 문자열을 생성합니다.

    Args:
        k (int, optional): 생성할 문자열의 길이 (Default: 29)

    Returns:
        str: 생성된 랜덤한 문자열
    """
    
    return "".join(random.choices(string.ascii_letters + string.digits, k=k))

def generate_api_key(salt: Optional[str] = None) -> str:
    """API Key를 생성합니다.

    Args:
        salt (Optional[str]): API Key 생성에 사용할 문자열 (Default: None)
    
    Returns:
        str: `jd-`로 시작하는 API Key
    """
    
    API_KEY_LENGTH = 48
    
    if salt:
        combined = (generate_id(k=API_KEY_LENGTH // 2) + salt).encode("utf-8")
        random_str = base64.urlsafe_b64encode(combined).decode("utf-8")[:API_KEY_LENGTH]
    else:
        random_str = generate_id(k=API_KEY_LENGTH)
    
    return f"jd-{random_str}"

async def asafety_call(func: Awaitable) -> Optional[Any]:
    """안전하게 비동기 함수를 호출합니다.

    Args:
        func (Awaitable): 호출할 비동기 함수

    Returns:
        Optional[Any]: 호출 결과 또는 None
    """
    
    try:
        return await func
    except Exception:
        return None

def serialize_datetime(value: datetime | date | time) -> str:
    """datetime, date, time 객체를 로컬 시간대로 변환하여 ISO 형식 문자열로 직렬화합니다.
    
    Args:
        value (datetime | date | time): 직렬화할 datetime, date 또는 time 객체
    
    Returns:
        str: ISO 형식의 문자열 또는 None
    """
    
    if value is None:
        return None
    
    if isinstance(value, datetime):
        # datetime 객체 처리
        try:
            local_tz = ZoneInfo(env.TZ)
        except Exception:
            local_tz = ZoneInfo("Asia/Seoul")
        
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo("UTC"))
        return value.astimezone(local_tz).isoformat()
    elif isinstance(value, date):
        # date 객체 처리 (tzinfo 없음)
        return value.isoformat()
    elif isinstance(value, time):
        # time 객체 처리
        return value.isoformat()
    
    # 지원하지 않는 타입
    return str(value)

def format_datetime(value: datetime | date | time, format_str: str = "%Y-%m-%d %H:%M:%S", tz: Optional[str] = None) -> str:
    """datetime, date, time 객체를 지정된 시간대로 변환하여 문자열로 포맷팅합니다.
    
    Args:
        value (datetime | date | time): 포맷팅할 datetime, date 또는 time 객체
        format_str (str): 날짜 형식 문자열 (Default: "%Y-%m-%d %H:%M:%S")
        tz (str): 시간대 문자열 (Default: Environment 설정)
    
    Returns:
        str: 포맷팅된 문자열 또는 None
    """
    
    if value is None:
        return None
    
    if isinstance(value, datetime):
        # datetime 객체 처리
        try:
            local_tz = ZoneInfo(tz or env.TZ)
        except Exception:
            local_tz = ZoneInfo("Asia/Seoul")
        
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo("UTC"))
        return value.astimezone(local_tz).strftime(format_str)
    elif isinstance(value, date):
        # date 객체 처리 (tzinfo 없음)
        return value.strftime(format_str)
    elif isinstance(value, time):
        # time 객체 처리
        return value.strftime(format_str)
    
    # 지원하지 않는 타입
    return str(value)

def is_docker_environment() -> bool:
    """Docker 환경에서 실행 중인지 확인합니다.
    
    Returns:
        bool: Docker 환경이면 True, 아니면 False
    """
    
    # 1. /.dockerenv 파일 존재 확인
    if os.path.exists("/.dockerenv"):
        return True
    
    # 2. cgroup 정보에서 docker 확인
    try:
        with open("/proc/1/cgroup", "r") as f:
            content = f.read()
            if "docker" in content or "containerd" in content:
                return True
    except (FileNotFoundError, PermissionError):
        pass
    
    # 3. 환경변수 확인
    docker_env_vars = ["DOCKER_CONTAINER", "CONTAINER", "KUBERNETES_SERVICE_HOST"]
    for var in docker_env_vars:
        if os.environ.get(var):
            return True
    
    return False

def parse_pipe_separated_string(value: Optional[str]) -> list[str]:
    """파이프(|)로 구분된 문자열을 리스트로 변환합니다.
    
    Args:
        value (Optional[str]): 파이프(|)로 구분된 문자열
    
    Returns:
        list[str]: 분리된 문자열 리스트 (공백 제거 및 빈 문자열 제외)
    """
    
    if value is None or len(value.strip()) == 0:
        return []
    
    return [item.strip() for item in value.split("|") if item.strip()]

def build_or_condition(field, values: list[str], operator="=="):
    """여러 값에 대한 OR 조건을 생성합니다.
    
    Args:
        field: 데이터베이스 필드
        values (list[str]): 조건에 사용할 값 목록
        operator (str): 연산자 ("==" 또는 "contains")
    
    Returns:
        조합된 OR 조건 또는 None
    """
    
    if not values:
        return None
    
    if operator == "contains":
        conditions = [field.contains(value) for value in values]
    else:  # operator == "=="
        conditions = [field == value for value in values]
    
    if len(conditions) == 1:
        return conditions[0]
    
    combined_condition = conditions[0]
    for condition in conditions[1:]:
        combined_condition = combined_condition | condition
    
    return combined_condition

def get_server_url(request: Request) -> str:
    """클라이언트가 요청한 서버의 URL을 생성합니다.

    Args:
        request (Request): FastAPI Request 객체

    Returns:
        str: 서버의 URL
    """
    
    # 헤더 데이터가 있는 경우 헤더 데이터 기준으로 생성
    headers = dict(request.headers)
    required_headers = ["x-forwarded-proto", "x-forwarded-host", "x-forwarded-port"]
    if all(header in headers for header in required_headers):
        proto = headers["x-forwarded-proto"]
        host = headers["x-forwarded-host"]
        port = headers["x-forwarded-port"]
        
        # HTTP/HTTPS 기본 포트인 경우 포트 생략
        if (proto == "http" and port == "80") or (proto == "https" and port == "443"):
            return f"{proto}://{host}{env.APP_ROOT_PATH.rstrip('/')}"
        else:
            return f"{proto}://{host}:{port}{env.APP_ROOT_PATH.rstrip('/')}"

    # URL 정보 기준으로 생성
    return f"{request.url.scheme}://{request.url.netloc}{env.APP_ROOT_PATH.rstrip('/')}"
