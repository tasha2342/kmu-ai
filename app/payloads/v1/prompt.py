from typing import Optional

from pydantic import BaseModel, Field


class AddPromptPayload(BaseModel):
    """프롬프트 추가 요청"""
    
    name: str = Field(
        ..., min_length=1, max_length=255,
        description="프롬프트 이름입니다. (고유값)",
        examples=["기본 시스템 프롬프트", "코드 리뷰 프롬프트"]
    )
    description: Optional[str] = Field(
        None, max_length=1024,
        description="프롬프트 설명입니다.",
        examples=["일반적인 대화를 위한 기본 시스템 프롬프트"]
    )
    content: str = Field(
        ..., min_length=1, max_length=65535,
        description=(
            "프롬프트 내용입니다. `{{변수명}}` 형태로 변수를 사용할 수 있습니다.  \n"
            "사용 가능한 변수는 다음과 같습니다:  \n"
            "- `{{now:포맷}}`: 현재 날짜와 시간 (포맷은 C# 형식을 사용합니다.)  \n"
            "  - [사용자 지정 날짜 및 시간 형식 문자열 - .NET | Microsoft Learn](https://learn.microsoft.com/ko-kr/dotnet/standard/base-types/custom-date-and-time-format-strings)  \n"
            "  - `{{now}}`는 기본 포맷인 `yyyy-MM-dd HH:mm:ss`로 치환됩니다.  \n"
			"- `{{user_name}}`: 사용자 이름  \n"
			"- `{{user_id}}`: 사용자 ID  \n"
			"- `{{변수명}}`: 프롬프트 렌더링 API의 variables 파라미터로 전달된 커스텀 변수들  \n"
		),
        examples=[
            (
                "당신은 유용한 AI 어시스턴트입니다.  \n"
                "현재 시간은 {{now:yyyy-MM-dd HH:mm:ss}} 입니다.  \n"
                "당신이 대화하는 사용자의 이름은 {{user_name}} 입니다."
			)
		]
    )
    tags: Optional[list[str]] = Field(
        None,
        description="태그 배열입니다.",
        examples=[["일반"], ["PoC", "고객상담"]]
    )
    access_scope: Optional[str] = Field(
        None,
        description=(
            "접근 가능 범위입니다.  \n"
            "- **NULL**: 모두 접근 가능  \n"
            "- **group:{group_name}**: 해당 그룹에 속한 사용자만 접근 가능  \n"
            "- **user:{user_name}**: 해당 사용자만 접근 가능  \n"
        ),
        examples=[None, "group:admin", "user:admin"]
    )


class UpdatePromptPayload(BaseModel):
    """프롬프트 수정 요청"""
    
    description: Optional[str] = Field(
        None, max_length=1024,
        description="프롬프트 설명입니다.",
        examples=["일반적인 대화를 위한 기본 시스템 프롬프트"]
    )
    content: Optional[str] = Field(
        None, min_length=1, max_length=65535,
        description=(
            "프롬프트 내용입니다.  \n"
            "내용이 변경되면 이전 내용은 백업됩니다."
        ),
        examples=["You are a helpful AI assistant. Current time: {{now:yyyy-MM-dd HH:mm:ss}}"]
    )
    change_note: Optional[str] = Field(
        None, max_length=1024,
        description=(
            "변경 사항 메모입니다.  \n"
            "프롬프트 내용이 변경될 때 변경 내용을 기록할 수 있습니다."
        ),
        examples=["시간 변수 추가", "프롬프트 내용 개선"]
    )
    tags: Optional[list[str]] = Field(
        None,
        description="태그 배열입니다.",
        examples=[["일반"], ["PoC", "고객상담"]]
    )
    access_scope: Optional[str] = Field(
        None,
        description=(
            "접근 가능 범위입니다.  \n"
            "- **NULL**: 모두 접근 가능  \n"
            "- **group:{group_name}**: 해당 그룹에 속한 사용자만 접근 가능  \n"
            "- **user:{user_name}**: 해당 사용자만 접근 가능  \n"
        ),
        examples=[None, "group:admin", "user:admin"]
    )


class RenderPromptPayload(BaseModel):
    """프롬프트 렌더링 요청"""
    
    variables: Optional[dict[str, str]] = Field(
        None,
        description=(
            "프롬프트에 삽입할 커스텀 변수입니다.  \n"
            "`{{변수명}}` 형태로 참조됩니다."
        ),
        examples=[
            {"department": "고객지원", "priority": "높음"},
            {"language": "한국어", "age": 30}
        ]
    )
