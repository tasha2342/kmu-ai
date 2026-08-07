from pydantic import BaseModel, Field

from app.models.enum import SourceType


class RunIngestionPayload(BaseModel):
    """수집 작업 실행 요청 데이터 (KAI-REQ-014)"""

    source_type: SourceType = Field(
        SourceType.FAQ,
        description=(
            "재색인할 원천 유형입니다.  \n"
            "`faq`(FAQ 지식베이스)와 `regulation`(`resources/regulations`의 학칙·규정 HWP)을 지원합니다.  \n"
            "`notice`/`document`는 학내 원천 데이터 연계가 제공되지 않아 요청 시 400을 반환합니다."
        ),
        examples=list(SourceType)
    )
    force: bool = Field(
        False,
        description=(
            "원문 변경이 없어도 강제로 재색인할지 여부입니다.  \n"
            "`true`로 설정하면 임베딩 텍스트 해시가 같아도 임베딩을 다시 생성합니다.  \n"
            "`regulation`은 문서 원문 해시가 같아도 HWP를 다시 추출·청킹·임베딩합니다.  \n"
            "임베딩 모델을 교체한 뒤 전량 재색인할 때 사용합니다."
        ),
        examples=[False, True]
    )
    only_stale: bool = Field(
        True,
        description=(
            "재색인이 필요한 항목만 대상으로 할지 여부입니다.  \n"
            "`true`(기본값)이면 색인 상태가 `stale`/`pending`/`failed`이거나 색인 레코드가 아예 없는 공개(`published`) FAQ만 처리합니다.  \n"
            "`false`이면 모든 공개 FAQ를 대상으로 합니다.  \n"
            "`regulation`은 대상 선정이 없어(디렉터리 전체 순회) `false`가 `force=true`와 같게 동작합니다."
        ),
        examples=[True, False]
    )
