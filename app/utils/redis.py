import functools
import orjson

from redis.asyncio import Redis

from typing import Callable, Any, AsyncGenerator, Optional

from pydantic import BaseModel

from contextlib import asynccontextmanager

from app.config import config


@asynccontextmanager
async def get_redis() -> AsyncGenerator[Redis, None]:
    """Redis 연결을 관리합니다.

    Yields:
        Redis: Redis 인스턴스
    """
    
    redis = Redis(
        host=config.redis.host,
        port=config.redis.port,
        decode_responses=True,
        retry_on_timeout=True,
        health_check_interval=30,
        max_connections=10,
    )
    
    try:
        yield redis
    finally:
        await redis.close()


def redis_cache(expire: int = 300, return_type: Optional[type] = None) -> Callable:
    """함수 실행 결과를 캐시에 저장하여, 동일한 인자로 호출되는 경우 캐시된 결과를 반환하는 데코레이터입니다.

    Args:
        expire (int, optional): 캐시 만료 시간 (초)
        return_type (type, optional): 반환 타입

    Returns:
        Callable: 데코레이터
    """
    
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            # 캐시 키 생성
            cache_key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
            
            async with get_redis() as redis:
                # Redis에서 캐시된 결과 확인
                cached_result = await redis.get(cache_key)
                
                if cached_result:
                    # 캐시된 결과가 있으면 반환
                    result = orjson.loads(cached_result)
                    if result:
                        return return_type(**result) if return_type else result
                
                # 캐시된 결과가 없으면 함수 실행
                result = await func(*args, **kwargs)
                if isinstance(result, BaseModel):
                    result = result.model_dump()
                
                # 결과를 Redis에 저장
                await redis.setex(
                    cache_key,
                    expire,
                    orjson.dumps(result).decode()
                )
                
                if result is not None:
                    return return_type(**result) if return_type else result
                return None
        return wrapper
    return decorator
