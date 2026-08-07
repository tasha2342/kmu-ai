from pydantic import BaseModel, Field

from uuid import UUID

from typing import Optional

from app.models.enum import FaqStatus, FaqVisibility, Language, VectorStatus
from app.utils.faq_service import FaqSearchResult, FaqSyncResult


class FaqCategoryInfo(BaseModel):
    """FAQ 카테고리 정보"""

    id: UUID = Field(
        ...,
        description="카테고리 ID입니다.",
        examples=["3f2b1c44-9a1e-4a5f-8b7c-2d1e0f9a8b7c"]
    )
    parent_id: Optional[UUID] = Field(
        None,
        description="상위 카테고리 ID입니다. 최상위인 경우 NULL입니다."
    )
    category_name: str = Field(
        ...,
        description="카테고리명입니다.",
        examples=["학사", "장학", "취업"]
    )
    category_code: str = Field(
        ...,
        description="카테고리 코드입니다.",
        examples=["ACADEMIC", "SCHOLARSHIP", "CAREER"]
    )
    department_code: Optional[str] = Field(
        None,
        description="담당 부서 코드입니다.",
        examples=["ACAD_AFFAIRS"]
    )
    display_order: int = Field(
        0,
        description="노출 순서입니다.",
        examples=[1, 2, 3]
    )
    is_active: bool = Field(
        True,
        description="사용 여부입니다.",
        examples=[True, False]
    )
    faq_count: int = Field(
        0,
        description="카테고리에 속한 FAQ 개수입니다.",
        examples=[12]
    )
    created_at: str = Field(
        ...,
        description="생성 날짜입니다."
    )
    updated_at: str = Field(
        ...,
        description="수정 날짜입니다."
    )

class FaqCategoryListResponse(BaseModel):
    """FAQ 카테고리 목록 응답"""

    categories: list[FaqCategoryInfo] = Field(
        ...,
        description="노출 순서로 정렬된 카테고리 리스트입니다."
    )
    total_count: int = Field(
        ...,
        description="총 카테고리 수입니다.",
        examples=[3]
    )


class FaqInfo(BaseModel):
    """FAQ 정보"""

    id: UUID = Field(
        ...,
        description="FAQ ID입니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    )
    category_id: UUID = Field(
        ...,
        description="소속 카테고리 ID입니다."
    )
    category_code: Optional[str] = Field(
        None,
        description="소속 카테고리 코드입니다.",
        examples=["ACADEMIC"]
    )
    category_name: Optional[str] = Field(
        None,
        description="소속 카테고리명입니다.",
        examples=["학사"]
    )
    question: str = Field(
        ...,
        description="대표 질문입니다.",
        examples=["수강신청 기간은 언제인가요?"]
    )
    answer: str = Field(
        ...,
        description="답변입니다.",
        examples=["2026학년도 1학기 수강신청은 2026년 2월 10일부터 2월 14일까지입니다."]
    )
    question_aliases_json: list[str] = Field(
        default_factory=list,
        description="유사 질문 목록입니다.",
        examples=[["수강신청 언제야", "수강신청 일정 알려줘"]]
    )
    tags_json: list[str] = Field(
        default_factory=list,
        description="태그 목록입니다.",
        examples=[["수강신청", "학사일정"]]
    )
    source_url: Optional[str] = Field(
        None,
        description="원문 URL입니다.",
        examples=["https://www.kmu.ac.kr/notice/1234"]
    )
    department_code: Optional[str] = Field(
        None,
        description="담당 부서 코드입니다.",
        examples=["ACAD_AFFAIRS"]
    )
    visibility: FaqVisibility = Field(
        FaqVisibility.PUBLIC,
        description="공개 범위입니다.",
        examples=list(FaqVisibility)
    )
    status: FaqStatus = Field(
        FaqStatus.DRAFT,
        description="상태입니다.",
        examples=list(FaqStatus)
    )
    language: Language = Field(
        Language.KO,
        description="언어입니다.",
        examples=list(Language)
    )
    version: int = Field(
        1,
        description="버전입니다. 내용이 수정될 때마다 증가합니다.",
        examples=[1, 2]
    )
    created_at: str = Field(
        ...,
        description="생성 날짜입니다."
    )
    updated_at: str = Field(
        ...,
        description="수정 날짜입니다."
    )

class FaqIndexInfo(BaseModel):
    """FAQ 색인 상태 정보"""

    embedding_model: str = Field(
        ...,
        description="색인에 사용된 임베딩 모델명입니다.",
        examples=["text-embedding-3-small"]
    )
    embedding_version: Optional[str] = Field(
        None,
        description="임베딩 스키마 버전입니다.",
        examples=["v1"]
    )
    embedding_text_hash: str = Field(
        ...,
        description="색인된 임베딩 텍스트의 SHA-256 해시입니다."
    )
    vector_status: VectorStatus = Field(
        VectorStatus.PENDING,
        description="벡터 색인 상태입니다.",
        examples=list(VectorStatus)
    )
    is_stale: bool = Field(
        False,
        description=(
            "현재 질문과 색인된 원문이 달라 재색인이 필요한지 여부입니다.  \n"
            "**true**이면 `/faq/sync`로 재색인해야 합니다."
        ),
        examples=[False, True]
    )
    indexed_at: Optional[str] = Field(
        None,
        description="마지막 색인 날짜입니다."
    )

class FaqDetailResponse(BaseModel):
    """FAQ 상세 응답"""

    faq: FaqInfo = Field(
        ...,
        description="FAQ 정보입니다."
    )
    index: Optional[FaqIndexInfo] = Field(
        None,
        description=(
            "색인 상태 정보입니다.  \n"
            "아직 색인된 적이 없으면 NULL입니다."
        )
    )

class FaqMutationResponse(BaseModel):
    """FAQ 생성·수정 응답"""

    message: str = Field(
        ...,
        description="응답 메시지입니다.",
        examples=["FAQ가 생성되었습니다."]
    )
    id: UUID = Field(
        ...,
        description="FAQ ID입니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    )
    version: int = Field(
        1,
        description="반영된 버전입니다.",
        examples=[1, 2]
    )
    indexed: bool = Field(
        False,
        description=(
            "이번 요청에서 색인이 완료되었는지 여부입니다.  \n"
            "공개 상태가 아니거나 색인이 필요하지 않으면 **false**입니다."
        ),
        examples=[False, True]
    )
    index_warning: Optional[str] = Field(
        None,
        description=(
            "색인 처리 중 발생한 경고 메시지입니다.  \n"
            "색인에 실패해도 FAQ 저장 자체는 정상 처리되며, 이후 `/faq/sync`로 재시도할 수 있습니다."
        ),
        examples=["FAQ 색인 중 오류가 발생했습니다. 이후 색인 동기화로 재시도해주세요."]
    )


class FaqSyncResponse(BaseModel):
    """FAQ 색인 동기화 응답"""

    total_count: int = Field(
        ...,
        description="동기화 대상 FAQ 수입니다.",
        examples=[42]
    )
    success_count: int = Field(
        ...,
        description="색인에 성공한 FAQ 수입니다.",
        examples=[40]
    )
    skipped_count: int = Field(
        ...,
        description="원문 변경이 없거나 공개 상태가 아니어서 건너뛴 FAQ 수입니다.",
        examples=[1]
    )
    failed_count: int = Field(
        ...,
        description="색인에 실패한 FAQ 수입니다.",
        examples=[1]
    )
    results: list[FaqSyncResult] = Field(
        default_factory=list,
        description="FAQ 건별 색인 결과입니다."
    )
    warning: Optional[str] = Field(
        None,
        description=(
            "동기화 준비 단계에서 발생한 경고 메시지입니다.  \n"
            "벡터 지식베이스 또는 임베딩 모델을 사용할 수 없는 경우 채워집니다."
        ),
        examples=["FAQ 지식베이스 컬렉션을 준비하지 못했습니다."]
    )


class FaqSearchResponse(BaseModel):
    """FAQ 유사도 검색 응답"""

    query: str = Field(
        ...,
        description="검색에 사용된 질문입니다.",
        examples=["수강신청 언제 하나요?"]
    )
    results: list[FaqSearchResult] = Field(
        default_factory=list,
        description="유사도 순으로 정렬된 검색 결과입니다."
    )
    total_count: int = Field(
        ...,
        description="검색 결과 수입니다.",
        examples=[3]
    )
    score_threshold: Optional[float] = Field(
        None,
        description="적용된 최소 유사도 점수입니다.",
        examples=[0.35]
    )
    latency_ms: int = Field(
        ...,
        description="검색에 소요된 시간(밀리초)입니다.",
        examples=[128]
    )
