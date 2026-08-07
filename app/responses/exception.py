from datetime import datetime

from typing import Optional

from fastapi import status
from fastapi.responses import ORJSONResponse

from app.models.api.exception import (
    BadRequestError,
    UnauthorizedError,
    ForbiddenError,
    NotFoundError,
    ConflictError,
    UnprocessableEntityError,
    UnprocessableEntityErrorTarget,
    TooManyRequestsError,
    InternalServerError,
    ServiceUnavailableError,
)


# 400
class BadRequestResponse(ORJSONResponse):
    def __init__(self, message: str, target: Optional[str] = None, **kwargs):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=BadRequestError(message=message, target=target).model_dump(),
            **kwargs
        )

# 401
class UnauthorizedResponse(ORJSONResponse):
    def __init__(self, message: str, **kwargs):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=UnauthorizedError(message=message).model_dump(),
            **kwargs
        )

# 403
class ForbiddenResponse(ORJSONResponse):
    def __init__(self, message: str, **kwargs):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            content=ForbiddenError(message=message).model_dump(),
            **kwargs
        )

# 404
class NotFoundResponse(ORJSONResponse):
    def __init__(self, message: str, target: Optional[str] = None, **kwargs):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            content=NotFoundError(message=message, target=target).model_dump(),
            **kwargs
        )

# 409
class ConflictResponse(ORJSONResponse):
    def __init__(self, message: str, target: Optional[str] = None, **kwargs):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            content=ConflictError(message=message, target=target).model_dump(),
            **kwargs
        )

# 422
class UnprocessableEntityResponse(ORJSONResponse):
    def __init__(self, message: str, targets: Optional[list[UnprocessableEntityErrorTarget]] = None, **kwargs):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=UnprocessableEntityError(message=message, targets=targets).model_dump(),
            **kwargs
        )

# 429
class TooManyRequestsResponse(ORJSONResponse):
    def __init__(self, message: str, limit: Optional[int] = None, cycle_value: Optional[int] = None, cycle_unit: Optional[str] = None, reset_at: datetime = None, **kwargs):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=TooManyRequestsError(message=message, limit=limit, cycle_value=cycle_value, cycle_unit=cycle_unit, reset_at=reset_at).model_dump(),
            **kwargs
        )

# 500
class InternalServerErrorResponse(ORJSONResponse):
    def __init__(self, message: str, **kwargs):
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=InternalServerError(message=message).model_dump(),
            **kwargs
        )

# 503
class ServiceUnavailableResponse(ORJSONResponse):
    def __init__(self, message: str, **kwargs):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ServiceUnavailableError(message=message).model_dump(),
            **kwargs
        )
