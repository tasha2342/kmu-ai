from datetime import datetime

from typing import Optional

from pydantic import BaseModel, Field, field_serializer

import app.utils.common as util
import app.models.db_item as db_items


class PromptListItem(BaseModel):
    """프롬프트 목록 항목 (간략 정보)"""
    
    id: int = Field(
        ...,
        description="프롬프트 ID입니다.",
        examples=[1, 2, 3]
    )
    name: str = Field(
        ...,
        description="프롬프트 이름입니다.",
        examples=["기본 시스템 프롬프트", "코드 리뷰 프롬프트"]
    )
    description: Optional[str] = Field(
        None,
        description="프롬프트 설명입니다.",
        examples=["일반적인 대화를 위한 기본 시스템 프롬프트"]
    )
    current_version: int = Field(
        ...,
        description="현재 버전 번호입니다.",
        examples=[1, 2, 3]
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
    is_system: bool = Field(
        False,
        description=(
            "시스템 전용 프롬프트 여부입니다.  \n"
            "목록/상세 조회 시 기본적으로 제외됩니다."
        ),
        examples=[False, True]
    )
    created_by: str = Field(
        ...,
        description="생성자 ID입니다.",
        examples=["68b3b2e0-0d96-4dd9-a1c3-313d5e388628"]
    )
    created_at: datetime = Field(
        ...,
        description="생성 날짜입니다.",
        examples=[util.get_now()]
    )
    updated_at: datetime = Field(
        ...,
        description="수정 날짜입니다.",
        examples=[util.get_now()]
    )
    
    
    @field_serializer("created_at", "updated_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)
    
    @classmethod
    def from_prompt(cls, prompt: db_items.Prompt) -> "PromptListItem":
        """Prompt 모델을 PromptListItem으로 변환합니다."""
        
        return cls(
            id=prompt.id,
            name=prompt.name,
            description=prompt.description,
            current_version=prompt.current_version,
            tags=prompt.tags,
            access_scope=prompt.access_scope,
            is_system=prompt.is_system,
            created_by=prompt.created_by,
            created_at=util.serialize_datetime(prompt.created_at) if isinstance(prompt.created_at, datetime) else prompt.created_at,
            updated_at=util.serialize_datetime(prompt.updated_at) if isinstance(prompt.updated_at, datetime) else prompt.updated_at,
        )


class PromptVersionListItem(BaseModel):
    """프롬프트 버전 목록 항목 (간략 정보)"""
    
    id: int = Field(
        ...,
        description="버전 ID입니다.",
        examples=[1, 2, 3]
    )
    prompt_id: int = Field(
        ...,
        description="프롬프트 ID입니다.",
        examples=[1, 2, 3]
    )
    version: int = Field(
        ...,
        description="버전 번호입니다.",
        examples=[1, 2, 3]
    )
    change_note: Optional[str] = Field(
        None,
        description="변경 사항 메모입니다.",
        examples=["초기 버전", "시간 변수 추가"]
    )
    created_by: str = Field(
        ...,
        description="수정자 ID입니다.",
        examples=["68b3b2e0-0d96-4dd9-a1c3-313d5e388628"]
    )
    created_at: datetime = Field(
        ...,
        description="생성 날짜입니다.",
        examples=[util.get_now()]
    )
    
    
    @field_serializer("created_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)
    
    @classmethod
    def from_version(cls, version: db_items.PromptVersion) -> "PromptVersionListItem":
        """PromptVersion 모델을 PromptVersionListItem으로 변환합니다."""
        
        return cls(
            id=version.id,
            prompt_id=version.prompt_id,
            version=version.version,
            change_note=version.change_note,
            created_by=version.created_by,
            created_at=util.serialize_datetime(version.created_at) if isinstance(version.created_at, datetime) else version.created_at,
        )


class RenderPromptResponse(BaseModel):
    """프롬프트 렌더링 응답"""
    
    rendered_content: str = Field(
        ...,
        description="변수가 치환된 최종 프롬프트 내용입니다.",
        examples=["You are a helpful AI assistant. Current time: 2026-01-09 15:30:00"]
    )
