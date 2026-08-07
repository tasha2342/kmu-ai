from pydantic import BaseModel, Field

from uuid import UUID

from typing import Optional

from app.models.enum import FaqStatus, FaqVisibility, Language


class CreateFaqCategoryPayload(BaseModel):
    """FAQ 카테고리 생성 요청 데이터"""

    category_name: str = Field(
        ..., min_length=1, max_length=100,
        description="카테고리명입니다.",
        examples=["학사", "장학", "취업"]
    )
    category_code: str = Field(
        ..., min_length=1, max_length=50,
        description=(
            "카테고리 코드입니다.  \n"
            "전체 카테고리에서 중복될 수 없습니다."
        ),
        examples=["ACADEMIC", "SCHOLARSHIP", "CAREER"]
    )
    parent_id: Optional[UUID] = Field(
        None,
        description=(
            "상위 카테고리 ID입니다.  \n"
            "최상위 카테고리인 경우 NULL입니다."
        )
    )
    department_code: Optional[str] = Field(
        None, max_length=50,
        description="담당 부서 코드입니다.",
        examples=["ACAD_AFFAIRS"]
    )
    display_order: int = Field(
        0, ge=0,
        description="노출 순서입니다. 값이 작을수록 먼저 노출됩니다.",
        examples=[1, 2, 3]
    )
    is_active: bool = Field(
        True,
        description="사용 여부입니다.",
        examples=[True, False]
    )


class UpdateFaqCategoryPayload(BaseModel):
    """FAQ 카테고리 수정 요청 데이터

    요청에 포함된 항목만 수정됩니다.
    """

    category_name: Optional[str] = Field(
        None, min_length=1, max_length=100,
        description="카테고리명입니다.",
        examples=["학사", "장학", "취업"]
    )
    category_code: Optional[str] = Field(
        None, min_length=1, max_length=50,
        description=(
            "카테고리 코드입니다.  \n"
            "전체 카테고리에서 중복될 수 없습니다."
        ),
        examples=["ACADEMIC", "SCHOLARSHIP", "CAREER"]
    )
    parent_id: Optional[UUID] = Field(
        None,
        description=(
            "상위 카테고리 ID입니다.  \n"
            "최상위 카테고리로 변경하려면 NULL을 전달합니다.  \n"
            "자기 자신을 상위 카테고리로 지정할 수 없습니다."
        )
    )
    department_code: Optional[str] = Field(
        None, max_length=50,
        description="담당 부서 코드입니다.",
        examples=["ACAD_AFFAIRS"]
    )
    display_order: Optional[int] = Field(
        None, ge=0,
        description="노출 순서입니다. 값이 작을수록 먼저 노출됩니다.",
        examples=[1, 2, 3]
    )
    is_active: Optional[bool] = Field(
        None,
        description="사용 여부입니다.",
        examples=[True, False]
    )


class CreateFaqPayload(BaseModel):
    """FAQ 생성 요청 데이터"""

    category_id: UUID = Field(
        ...,
        description="소속 카테고리 ID입니다. 등록된 카테고리여야 합니다."
    )
    question: str = Field(
        ..., min_length=1,
        description="대표 질문입니다. 유사도 검색의 색인 대상입니다.",
        examples=["수강신청 기간은 언제인가요?"]
    )
    answer: str = Field(
        ..., min_length=1,
        description="답변입니다.",
        examples=["2026학년도 1학기 수강신청은 2026년 2월 10일부터 2월 14일까지입니다."]
    )
    question_aliases_json: list[str] = Field(
        default_factory=list,
        description=(
            "유사 질문 목록입니다.  \n"
            "대표 질문과 함께 임베딩되어 구어체 질의의 검색 정확도를 높입니다."
        ),
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
        None, max_length=50,
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
        description=(
            "상태입니다.  \n"
            "**published**로 생성하면 생성 직후 색인을 시도합니다."
        ),
        examples=list(FaqStatus)
    )
    language: Language = Field(
        Language.KO,
        description="언어입니다.",
        examples=list(Language)
    )


class UpdateFaqPayload(BaseModel):
    """FAQ 수정 요청 데이터

    요청에 포함된 항목만 수정됩니다.
    """

    category_id: Optional[UUID] = Field(
        None,
        description="소속 카테고리 ID입니다. 등록된 카테고리여야 합니다."
    )
    question: Optional[str] = Field(
        None, min_length=1,
        description=(
            "대표 질문입니다.  \n"
            "변경되면 기존 색인을 재색인 대상으로 표시한 뒤 재색인을 시도합니다."
        ),
        examples=["수강신청 기간은 언제인가요?"]
    )
    answer: Optional[str] = Field(
        None, min_length=1,
        description="답변입니다.",
        examples=["2026학년도 1학기 수강신청은 2026년 2월 10일부터 2월 14일까지입니다."]
    )
    question_aliases_json: Optional[list[str]] = Field(
        None,
        description=(
            "유사 질문 목록입니다.  \n"
            "변경되면 기존 색인을 재색인 대상으로 표시한 뒤 재색인을 시도합니다."
        ),
        examples=[["수강신청 언제야", "수강신청 일정 알려줘"]]
    )
    tags_json: Optional[list[str]] = Field(
        None,
        description="태그 목록입니다.",
        examples=[["수강신청", "학사일정"]]
    )
    source_url: Optional[str] = Field(
        None,
        description="원문 URL입니다.",
        examples=["https://www.kmu.ac.kr/notice/1234"]
    )
    department_code: Optional[str] = Field(
        None, max_length=50,
        description="담당 부서 코드입니다.",
        examples=["ACAD_AFFAIRS"]
    )
    visibility: Optional[FaqVisibility] = Field(
        None,
        description="공개 범위입니다.",
        examples=list(FaqVisibility)
    )
    status: Optional[FaqStatus] = Field(
        None,
        description=(
            "상태입니다.  \n"
            "**published**가 아닌 상태로 변경하면 색인된 벡터가 제거됩니다."
        ),
        examples=list(FaqStatus)
    )
    language: Optional[Language] = Field(
        None,
        description="언어입니다.",
        examples=list(Language)
    )


class SyncFaqPayload(BaseModel):
    """FAQ 색인 동기화 요청 데이터"""

    faq_ids: Optional[list[UUID]] = Field(
        None,
        description=(
            "동기화할 FAQ ID 목록입니다.  \n"
            "지정하지 않으면 공개(**published**) 상태의 전체 FAQ를 동기화합니다."
        )
    )
    force: bool = Field(
        False,
        description=(
            "원문 변경이 없어도 강제로 재색인할지 여부입니다.  \n"
            "임베딩 모델을 교체한 경우에 사용합니다."
        ),
        examples=[False, True]
    )


class SearchFaqPayload(BaseModel):
    """FAQ 유사도 검색 요청 데이터"""

    query: str = Field(
        ..., min_length=1,
        description="검색할 질문입니다.",
        examples=["수강신청 언제 하나요?"]
    )
    top_k: Optional[int] = Field(
        None, ge=1, le=50,
        description=(
            "반환할 결과 수입니다.  \n"
            "지정하지 않으면 챗봇 설정값을 사용합니다."
        ),
        examples=[3, 5, 10]
    )
    score_threshold: Optional[float] = Field(
        None, ge=0.0, le=1.0,
        description=(
            "최소 유사도 점수입니다.  \n"
            "지정하지 않으면 챗봇 설정값을 사용합니다."
        ),
        examples=[0.35, 0.5]
    )
    language: Optional[Language] = Field(
        None,
        description="언어 필터입니다.",
        examples=list(Language)
    )
    category_code: Optional[str] = Field(
        None, max_length=50,
        description="카테고리 코드 필터입니다.",
        examples=["ACADEMIC", "SCHOLARSHIP"]
    )
