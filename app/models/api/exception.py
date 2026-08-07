from datetime import datetime

from typing import Optional, Any, Union

from pydantic import BaseModel, Field, field_serializer

import app.utils.common as util


# 400
class BadRequestError(BaseModel):
    message: str = Field(
        ...,
        description="오류 메시지입니다.",
        examples=["요청이 올바르지 않습니다."],
    )
    target: Optional[str] = Field(
        None,
        description="잘못 요청된 항목입니다.",
        examples=["email=abc@com"],
    )

# 401
class UnauthorizedError(BaseModel):
    message: str = Field(
        ...,
        description="오류 메시지입니다.",
        examples=["인증 정보가 올바르지 않습니다."],
    )

# 403
class ForbiddenError(BaseModel):
    message: str = Field(
        ...,
        description="오류 메시지입니다.",
        examples=["접근 권한이 없습니다."],
    )

# 404
class NotFoundError(BaseModel):
    message: str = Field(
        ...,
        description="오류 메시지입니다.",
        examples=["페이지를 찾을 수 없습니다.", "항목을 찾을 수 없습니다."],
    )
    target: Optional[str] = Field(
        None,
        description="찾을 수 없는 항목입니다.",
        examples=["name=홍길동", "/api/v1/example/1"],
    )

# 409
class ConflictError(BaseModel):
    message: str = Field(
        ...,
        description="오류 메시지입니다.",
        examples=["항목이 이미 존재합니다."],
    )
    target: Optional[str] = Field(
        None,
        description="이미 존재하는 항목입니다.",
        examples=["name=홍길동"],
    )

# 422
class UnprocessableEntityErrorTarget(BaseModel):
    type: str = Field(
        ...,
        description="오류 타입입니다.",
        examples=["int_parsing"],
    )
    location: list[Union[str, int]] = Field(
        ...,
        description="오류 발생 위치입니다.",
        examples=[["query", "page"]],
    )
    message: str = Field(
        ...,
        description="오류 메시지입니다.",
        examples=["Input should be a valid integer, unable to parse string as an integer"],
    )
    input: Optional[Any] = Field(
        None,
        description="오류가 발생한 입력 값입니다.",
        examples=["test"],
    )

class UnprocessableEntityError(BaseModel):
    message: str = Field(
        ...,
        description="오류 메시지입니다.",
        examples=["요청 데이터가 올바르지 않습니다."],
    )
    targets: Optional[list[UnprocessableEntityErrorTarget]] = Field(
        None,
        description="오류가 발생한 항목입니다.",
    )

# 429
class TooManyRequestsError(BaseModel):
    message: str = Field(
        ...,
        description="오류 메시지입니다.",
        examples=["요청이 제한되었습니다: {cycle_value}{cycle_name}마다 {limit}회 요청 제한"],
    )
    limit: Optional[int] = Field(
        None,
        description="제한 주기 내 허용되는 최대 요청 횟수입니다.",
        examples=[10],
    )
    cycle_value: Optional[int] = Field(
        None,
        description="제한 주기의 값입니다.",
        examples=[1],
    )
    cycle_unit: Optional[str] = Field(
        None,
        description=(
            "제한 주기의 단위입니다.  \n"
            "- **day**: 일  \n"
            "- **month**: 달(월)  \n"
            "- **year**: 년  \n"
            "- **hour**: 시간  \n"
            "- **minute**: 분  \n"
            "- **second**: 초  \n"
        ),
        examples=["day"],
    )
    reset_at: Optional[datetime] = Field(
        None,
        description="요청 제한이 초기화되는 날짜입니다.",
        examples=[util.get_now()],
    )
    
    
    @field_serializer("reset_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

# 500
class InternalServerError(BaseModel):
    message: str = Field(
        ...,
        description="오류 메시지입니다.",
        examples=["서버 내부 오류가 발생했습니다."],
    )

# 503
class ServiceUnavailableError(BaseModel):
    message: str = Field(
        ...,
        description="오류 메시지입니다.",
        examples=["서비스를 사용할 수 없습니다."],
    )
