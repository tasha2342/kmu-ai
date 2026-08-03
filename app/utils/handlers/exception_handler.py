from typing import Tuple

from datetime import datetime

from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, status
from fastapi.responses import ORJSONResponse
from fastapi.exceptions import RequestValidationError

from keycloak import KeycloakError

from limits.limits import RateLimitItem

from slowapi.errors import RateLimitExceeded

from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import env
from app.responses.exception import (
    BadRequestResponse,
    UnauthorizedResponse,
    ForbiddenResponse,
    NotFoundResponse,
    ConflictResponse,
    UnprocessableEntityResponse,
    UnprocessableEntityErrorTarget,
    TooManyRequestsResponse,
    InternalServerErrorResponse,
    ServiceUnavailableResponse,
)
from app.utils.limiter import limiter


def init(app: FastAPI):
    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(request: Request, exc: RequestValidationError):
        return UnprocessableEntityResponse(
            message="요청 데이터가 올바르지 않습니다.",
            targets=[
                UnprocessableEntityErrorTarget(
                    type=error.get("type"),
                    location=error.get("loc"),
                    message=error.get("msg"),
                    input=error.get("input")
                ) for error in exc.errors()
            ]
        )
    
    @app.exception_handler(RateLimitExceeded)
    def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
        cycle_unit = exc.limit.limit.GRANULARITY.name
        cycle_unit_ko = ""
        
        match cycle_unit:
            case "day":
                cycle_unit_ko = "일"
            case "month":
                cycle_unit_ko = "달"
            case "year":
                cycle_unit_ko = "년"
            case "hour":
                cycle_unit_ko = "시간"
            case "minute":
                cycle_unit_ko = "분"
            case "second":
                cycle_unit_ko = "초"

        view_rate_limit: Tuple[RateLimitItem, list[str]] = request.state.view_rate_limit
        window_stats: Tuple[int, int] = limiter.limiter.get_window_stats(
            view_rate_limit[0], *view_rate_limit[1]
        )
        reset_in = 1 + window_stats[0]
        
        response = TooManyRequestsResponse(
            message=f"요청이 제한되었습니다: {exc.limit.limit.multiples}{cycle_unit_ko}마다 {exc.limit.limit.amount}회 요청 제한",
            limit=exc.limit.limit.amount,
            cycle_value=exc.limit.limit.multiples,
            cycle_unit=cycle_unit,
            reset_at=datetime.fromtimestamp(reset_in, tz=ZoneInfo(env.TZ))
        )
        response = request.app.state.limiter._inject_headers(
            response, request.state.view_rate_limit
        )
        return response
    
    @app.exception_handler(KeycloakError)
    async def keycloak_error_handler(request: Request, exc: KeycloakError):
        response_code = getattr(exc, "response_code", None) or status.HTTP_500_INTERNAL_SERVER_ERROR
        if response_code >= 500:
            return ServiceUnavailableResponse(
                message="사용자 인증/인가 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요."
            )

        return ORJSONResponse(content={"message": str(exc.error_message)}, status_code=response_code)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        if exc.status_code == status.HTTP_400_BAD_REQUEST:
            message = exc.detail if exc.detail != "Bad Request" else "잘못된 요청입니다."
            return BadRequestResponse(message=message)
        elif exc.status_code == status.HTTP_401_UNAUTHORIZED:
            # / (viewer) 경로만 FastAPI 기본 핸들러로 위임, 그 외는 기존 UnauthorizedResponse
            if request.url.path == "/":
                return await http_exception_handler(request, exc)
            else:
                return UnauthorizedResponse(message="인증 정보가 올바르지 않습니다.")
        elif exc.status_code == status.HTTP_403_FORBIDDEN:
            message = exc.detail if exc.detail != "Forbidden" else "접근 권한이 없습니다."
            return ForbiddenResponse(message=message)
        elif exc.status_code == status.HTTP_404_NOT_FOUND:
            message = exc.detail if exc.detail != "Not Found" else "페이지를 찾을 수 없습니다."
            return NotFoundResponse(message=message, target=request.url.path)
        elif exc.status_code == status.HTTP_409_CONFLICT:
            message = exc.detail if exc.detail != "Conflict" else "항목이 이미 존재합니다."
            return ConflictResponse(message=message)
        elif exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            message = exc.detail if exc.detail != "Too Many Requests" else "요청이 제한되었습니다."
            return TooManyRequestsResponse(message=message)
        elif exc.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR:
            message = exc.detail if exc.detail != "Internal Server Error" else "서버 내부 오류가 발생했습니다."
            return InternalServerErrorResponse(message=message)
        elif exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
            message = exc.detail if exc.detail != "Service Unavailable" else "서비스를 사용할 수 없습니다."
            return ServiceUnavailableResponse(message=message)
        else:
            return ORJSONResponse(content={"message": exc.detail}, status_code=exc.status_code)
