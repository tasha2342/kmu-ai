from pydantic import BaseModel, Field

from typing import Optional, Union

from app.models.enum import ModelType
from app.models.cost import (
    TextGenerationCost, EmbeddingCost, RerankCost,
    TextGenerationUsage, EmbeddingUsage, RerankUsage,
)


class MonthOverMonthChange(BaseModel):
    """지난달 대비 변동률"""

    total_requests: float = Field(
        ...,
        description="총 요청 수 변동률 (%)입니다. 지난달 데이터가 없으면 0입니다.",
        examples=[12.5]
    )
    estimated_cost: float = Field(
        ...,
        description="예상 비용 변동률 (%)입니다. 지난달 데이터가 없으면 0입니다.",
        examples=[-5.3]
    )
    success_rate: float = Field(
        ...,
        description="성공률 변동 (pp)입니다. 지난달 데이터가 없으면 0입니다.",
        examples=[1.2]
    )

class OverviewStats(BaseModel):
    """전체 개요 통계"""

    total_requests: int = Field(
        ...,
        description="총 요청 수입니다.",
        examples=[5000]
    )
    success_rate: float = Field(
        ...,
        description="성공률 (%)입니다.",
        examples=[98.2]
    )
    estimated_cost: float = Field(
        ...,
        description="예상 비용 (USD)입니다.",
        examples=[12.35]
    )
    active_models: int = Field(
        ...,
        description="활성화된 모델 수입니다.",
        examples=[5]
    )
    mom_change: MonthOverMonthChange = Field(
        ...,
        description="지난달 대비 변동률입니다."
    )


class DailyUsage(BaseModel):
    """일별 사용량"""

    date: str = Field(
        ...,
        description="날짜입니다. (YYYY-MM-DD)",
        examples=["2026-03-01"]
    )
    total_requests: int = Field(
        ...,
        description="총 요청 수입니다.",
        examples=[150]
    )
    success_count: int = Field(
        ...,
        description="성공한 요청 수입니다.",
        examples=[145]
    )
    error_count: int = Field(
        ...,
        description="실패한 요청 수입니다.",
        examples=[5]
    )
    estimated_cost: float = Field(
        ...,
        description="예상 비용 (USD)입니다.",
        examples=[0.42]
    )

class ModelUsageStats(BaseModel):
    """모델별 사용량 통계"""

    model_name: str = Field(
        ...,
        description="모델명입니다.",
        examples=["gpt-4o-mini"]
    )
    model_type: ModelType = Field(
        ...,
        description="모델 타입입니다.",
        examples=list(ModelType)
    )
    total_requests: int = Field(
        ...,
        description="총 요청 수입니다.",
        examples=[500]
    )
    success_rate: float = Field(
        ...,
        description="성공률 (%)입니다.",
        examples=[98.5]
    )
    usage_details: Union[TextGenerationUsage, EmbeddingUsage, RerankUsage] = Field(
        ...,
        description="모델 타입별 사용량 상세입니다.",
    )
    cost_info: Optional[Union[TextGenerationCost, EmbeddingCost, RerankCost]] = Field(
        None,
        description=(
            "모델에 설정된 비용 정보입니다.  \n"
            "비용이 설정되지 않은 경우 null입니다."
        ),
    )
    estimated_cost: float = Field(
        ...,
        description="예상 비용 (USD)입니다.",
        examples=[3.25]
    )

class UserUsageStats(BaseModel):
    """사용자별 사용량 통계 (관리자용)"""

    user_name: str = Field(
        ...,
        description="사용자명입니다.",
        examples=["admin"]
    )
    total_requests: int = Field(
        ...,
        description="총 요청 수입니다.",
        examples=[250]
    )
    estimated_cost: float = Field(
        ...,
        description="예상 비용 (USD)입니다.",
        examples=[5.80]
    )

class DashboardStatsResponse(BaseModel):
    """대시보드 통계 응답"""

    month: str = Field(
        ...,
        description="조회 월입니다. (YYYY-MM)",
        examples=["2026-03"]
    )
    overview: OverviewStats = Field(
        ...,
        description="전체 개요 통계입니다."
    )
    daily_usage: list[DailyUsage] = Field(
        ...,
        description="일별 사용량 목록입니다."
    )
    model_usage: list[ModelUsageStats] = Field(
        ...,
        description="모델별 사용량 통계 목록입니다."
    )
    user_usage: Optional[list[UserUsageStats]] = Field(
        None,
        description="사용자별 사용량 통계 목록입니다. (관리자만 조회 가능)"
    )
