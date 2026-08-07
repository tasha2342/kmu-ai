from datetime import datetime

from typing import Optional

from app.models.api.exception import (
    BadRequestError,
    UnauthorizedError,
    ForbiddenError,
    NotFoundError,
    ConflictError,
    UnprocessableEntityErrorTarget,
    UnprocessableEntityError,
    TooManyRequestsError,
    InternalServerError,
    ServiceUnavailableError,
)


HTTP_400 = {"description": "요청이 올바르지 않습니다.", "model": BadRequestError}
HTTP_401 = {"description": "인증 정보가 올바르지 않습니다.", "model": UnauthorizedError}
HTTP_403 = {"description": "접근 권한이 없습니다.", "model": ForbiddenError}
HTTP_404 = {"description": "항목을 찾을 수 없습니다.", "model": NotFoundError}
HTTP_409 = {"description": "항목이 이미 존재합니다.", "model": ConflictError}
HTTP_422 = {"description": "요청 데이터가 올바르지 않습니다.", "model": UnprocessableEntityError}
HTTP_429 = {"description": "요청이 제한되었습니다.", "model": TooManyRequestsError}
HTTP_500 = {"description": "서버 내부 오류가 발생했습니다.", "model": InternalServerError}
HTTP_503 = {"description": "서비스를 사용할 수 없습니다.", "model": ServiceUnavailableError}

DEFAULT_EXCEPTION_RESPONSES = {
    422: HTTP_422,
    500: HTTP_500,
    503: HTTP_503,
}

DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED = {
    **DEFAULT_EXCEPTION_RESPONSES,
    401: HTTP_401,
}

DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN = {
    **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    403: HTTP_403,
}


# Base
class BaseApiException(Exception):
    def to_response(self):
        from app.responses.exception import (
            BadRequestResponse,
            UnauthorizedResponse,
            ForbiddenResponse,
            NotFoundResponse,
            ConflictResponse,
            UnprocessableEntityResponse,
            TooManyRequestsResponse,
            InternalServerErrorResponse,
            ServiceUnavailableResponse,
        )
        
        if isinstance(self, BadRequestException):
            return BadRequestResponse(message=self.message, target=self.target)
        elif isinstance(self, UnauthorizedException):
            return UnauthorizedResponse(message=self.message)
        elif isinstance(self, ForbiddenException):
            return ForbiddenResponse(message=self.message)
        elif isinstance(self, NotFoundException):
            return NotFoundResponse(message=self.message, target=self.target)
        elif isinstance(self, ConflictException):
            return ConflictResponse(message=self.message, target=self.target)
        elif isinstance(self, UnprocessableEntityException):
            return UnprocessableEntityResponse(message=self.message, targets=self.targets)
        elif isinstance(self, TooManyRequestsException):
            return TooManyRequestsResponse(
                message=self.message,
                limit=self.limit,
                cycle_value=self.cycle_value,
                cycle_unit=self.cycle_unit,
                reset_at=self.reset_at,
            )
        elif isinstance(self, InternalServerErrorException):
            return InternalServerErrorResponse(message=self.message)
        elif isinstance(self, ServiceUnavailableException):
            return ServiceUnavailableResponse(message=self.message)
        else:
            raise NotImplementedError("지원되지 않는 예외 타입입니다.")

# 400
class BadRequestException(BaseApiException):
    def __init__(self, message: str, target: Optional[str] = None):
        self.message = message
        self.target = target

# 401
class UnauthorizedException(BaseApiException):
    def __init__(self, message: str):
        self.message = message

# 403
class ForbiddenException(BaseApiException):
    def __init__(self, message: str):
        self.message = message

# 404
class NotFoundException(BaseApiException):
    def __init__(self, message: str, target: Optional[str] = None):
        self.message = message
        self.target = target

# 409
class ConflictException(BaseApiException):
    def __init__(self, message: str, target: Optional[str] = None):
        self.message = message
        self.target = target

# 422
class UnprocessableEntityException(BaseApiException):
    def __init__(self, message: str, targets: Optional[list[UnprocessableEntityErrorTarget]] = None):
        self.message = message
        self.targets = targets

# 429
class TooManyRequestsException(BaseApiException):
    def __init__(
        self,
        message: str,
        limit: Optional[int] = None,
        cycle_value: Optional[int] = None,
        cycle_unit: Optional[str] = None,
        reset_at: Optional[datetime] = None,
    ):
        self.message = message
        self.limit = limit
        self.cycle_value = cycle_value
        self.cycle_unit = cycle_unit
        self.reset_at = reset_at

# 500
class InternalServerErrorException(BaseApiException):
    def __init__(self, message: str):
        self.message = message

# 503
class ServiceUnavailableException(BaseApiException):
    def __init__(self, message: str):
        self.message = message
