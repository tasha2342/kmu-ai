from logging import Logger

from fastapi import APIRouter, Depends

from app.exceptions.api_exception import (
    DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
)
from app.responses.v1.api_key import (
	ApiKeyResponse,
)
from app.models.auth import TokenUserInfo
from app.utils.logger import get_api_logger
import app.utils.auth as auth
import app.utils.common as util


router = APIRouter()


@router.get("/info", summary="API Key 조회",
    description=(
        "로그인한 사용자의 현재 API Key를 조회합니다.  \n"
        "API Key가 없는 경우 새로 발급 후 조회합니다."
    ),
    responses={
        200: {"description": "API Key 조회 결과를 반환합니다.", "model": ApiKeyResponse},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def get_api_key(
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 사용자 속성에서 API Key 조회
    api_key = user_info.attributes.get("apiKey", None)
    
    # 리스트로 된 경우 첫 번째 값 사용
    if api_key and isinstance(api_key, list):
        api_key = api_key[0]
    
    # API Key가 없는 경우 새로 발급
    if api_key is None or not isinstance(api_key, str) or not api_key.startswith("jd-"):
        api_key = util.generate_api_key(salt=user_info.sub)
        await auth.set_user_attributes(user_info, "apiKey", api_key)
        logger.info(f"API Key가 발급되었습니다. (user={user_info.username})")
    
    return ApiKeyResponse(
        api_key=api_key
    )

@router.post("/generate", summary="API Key 발급",
    description=(
        "로그인한 사용자에게 새로운 API Key를 발급합니다.  \n"
        "이미 API Key가 있는 경우에 새로운 API Key로 재발급됩니다."
    ),
    responses={
        200: {"description": "API Key 발급 결과를 반환합니다.", "model": ApiKeyResponse},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def generate_api_key(
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # API Key 생성
    api_key = util.generate_api_key(salt=user_info.sub)
    
    # 사용자 속성에 API Key 저장
    await auth.set_user_attributes(user_info, "apiKey", api_key)
    
    logger.info(f"API Key가 발급되었습니다. (user={user_info.username})")
    
    return ApiKeyResponse(
        api_key=api_key
    )
