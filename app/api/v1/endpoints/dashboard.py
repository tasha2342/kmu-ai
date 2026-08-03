import calendar

from collections import defaultdict

from logging import Logger

from datetime import datetime

from typing import Optional, Union

from peewee import fn, SQL

from fastapi import APIRouter, Depends, Query

from app.config import config
from app.exceptions.api_exception import (
    DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
)
from app.responses.exception import BadRequestResponse
from app.responses.v1.dashboard import (
    MonthOverMonthChange,
    OverviewStats,
    DailyUsage,
    ModelUsageStats,
    UserUsageStats,
    DashboardStatsResponse,
)
from app.models.api.exception import (
    BadRequestError,
)
from app.models.auth import TokenUserInfo
from app.models.enum import ModelType, ModelStatus, ModelUsageStatus
from app.models.cost import (
    TextGenerationCost, EmbeddingCost, RerankCost,
    TextGenerationUsage, EmbeddingUsage, RerankUsage,
)
from app.utils.logger import get_api_logger
from app.utils.database import DatabaseManager, get_db_manager
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.auth as auth
import app.utils.common as util


router = APIRouter()


def _calculate_cost(
    model_type: str,
    usage: dict,
    cost_info: Optional[Union[TextGenerationCost, EmbeddingCost, RerankCost]],
    request_count: int = 1,
) -> float:
    """모델 타입과 usage/cost 정보를 기반으로 예상 비용을 계산합니다.
    
    Args:
        model_type (str): 모델 타입
        usage (dict): 사용량 정보
        cost_info (Optional[Union[TextGenerationCost, EmbeddingCost, RerankCost]]): 비용 정보
        request_count (int): 요청 수

    Returns:
        float: 예상 비용
    """
    
    if not cost_info:
        return 0.0

    if model_type == ModelType.TEXT_GENERATION.value and isinstance(cost_info, TextGenerationCost):
        input_tokens = usage.get("input_tokens", 0)
        cached_input_tokens = usage.get("cached_input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)

        return (
            input_tokens * cost_info.input_tokens
            + cached_input_tokens * (cost_info.cached_input_tokens or 0)
            + output_tokens * cost_info.output_tokens
        ) / 1_000_000
    elif model_type == ModelType.EMBEDDING.value and isinstance(cost_info, EmbeddingCost):
        single_tokens = usage.get("single_tokens", 0)
        batch_tokens = usage.get("batch_tokens", 0)

        return (
            single_tokens * cost_info.single_tokens
            + batch_tokens * (cost_info.batch_tokens or 0)
        ) / 1_000_000
    elif model_type == ModelType.RERANK.value and isinstance(cost_info, RerankCost):
        return (request_count / 1_000) * cost_info.searches

    return 0.0


@router.get("/stats", summary="대시보드 통계 조회",
    description=(
        "대시보드 통계를 월 단위로 조회합니다.  \n"
        "- 관리자: 전체 사용자 또는 특정 사용자의 통계를 조회할 수 있습니다.  \n"
        "- 일반 사용자: 본인의 통계만 조회할 수 있습니다.  \n"
        "- 기본 조회 기간: 현재 월"
    ),
    responses={
        200: {"description": "대시보드 통계를 반환합니다.", "model": DashboardStatsResponse},
        400: {
            "description": "잘못된 요청입니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "future_month": {
                            "summary": "미래의 월은 조회할 수 없습니다.",
                            "value": {
                                "message": "미래의 월은 조회할 수 없습니다.",
                                "target": "month={year:04d}-{mon:02d}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def get_dashboard_stats(
    month: Optional[str] = Query(
        None,
        description="조회할 월입니다. (YYYY-MM, 기본값: 현재 월)",
        examples=["2026-03"]
    ),
    user_name: Optional[str] = Query(
        None,
        description="사용자명으로 필터링합니다. (관리자만 사용 가능)",
        examples=["user1"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 관리자 여부 확인
    is_admin = any(role in user_info.roles for role in config.auth.admin_roles)
    
    # 조회 대상 사용자 결정
    if is_admin and user_name:
        target_user_name = user_name
    elif is_admin:
        target_user_name = None  # 전체 사용자
    else:
        target_user_name = user_info.username

    # 월 파싱
    now = util.get_now()
    if month:
        year, mon = map(int, month.split("-"))
    else:
        year, mon = now.year, now.month

    if (year, mon) > (now.year, now.month):
        return BadRequestResponse(
            message="미래의 월은 조회할 수 없습니다.",
            target=f"month={year:04d}-{mon:02d}"
        )

    month_str = f"{year:04d}-{mon:02d}"
    last_day = calendar.monthrange(year, mon)[1]
    period_start = datetime(year, mon, 1, 0, 0, 0, tzinfo=now.tzinfo)
    period_end = datetime(year, mon, last_day, 23, 59, 59, tzinfo=now.tzinfo)

    # 지난달 기간 계산
    if mon == 1:
        prev_year, prev_mon = year - 1, 12
    else:
        prev_year, prev_mon = year, mon - 1
    prev_last_day = calendar.monthrange(prev_year, prev_mon)[1]
    prev_start = datetime(prev_year, prev_mon, 1, 0, 0, 0, tzinfo=now.tzinfo)
    prev_end = datetime(prev_year, prev_mon, prev_last_day, 23, 59, 59, tzinfo=now.tzinfo)

    # 기본 조건
    base_conditions = [
        db_models.ModelUsage.created_at >= period_start,
        db_models.ModelUsage.created_at <= period_end,
    ]
    
    if target_user_name:
        base_conditions.append(db_models.ModelUsage.user_name == target_user_name)

    def apply_conditions(query):
        for condition in base_conditions:
            query = query.where(condition)
        return query

    # 모델별 cost 정보 캐싱
    all_models_query = db_models.Model.select()
    all_models: list[db_items.Model] = await db_manager.select_items(all_models_query)
    model_cost_map: dict[str, Optional[dict]] = {}
    model_type_map: dict[str, str] = {}
    for m in all_models:
        model_cost_map[m.name] = m.cost
        model_type_map[m.name] = m.model_type.value if hasattr(m.model_type, "value") else str(m.model_type)

    # 핵심 집계 쿼리: 일별 + 모델별 그룹화
    agg_query = db_models.ModelUsage.select(
        fn.DATE(db_models.ModelUsage.created_at).alias("date"),
        db_models.ModelUsage.model_name,
        db_models.ModelUsage.model_type,
        fn.COUNT(db_models.ModelUsage.id).alias("total_requests"),
        fn.COUNT(db_models.ModelUsage.id).filter(
            db_models.ModelUsage.status == ModelUsageStatus.SUCCESS.value
        ).alias("success_count"),
        fn.COUNT(db_models.ModelUsage.id).filter(
            db_models.ModelUsage.status == ModelUsageStatus.ERROR.value
        ).alias("error_count"),
        # text_generation fields
        fn.COALESCE(fn.SUM(SQL("COALESCE((usage->>'input_tokens')::bigint, 0)")), 0).alias("sum_input_tokens"),
        fn.COALESCE(fn.SUM(SQL("COALESCE((usage->>'cached_input_tokens')::bigint, 0)")), 0).alias("sum_cached_input_tokens"),
        fn.COALESCE(fn.SUM(SQL("COALESCE((usage->>'output_tokens')::bigint, 0)")), 0).alias("sum_output_tokens"),
        # embedding fields
        fn.COALESCE(fn.SUM(SQL("COALESCE((usage->>'single_tokens')::bigint, 0)")), 0).alias("sum_single_tokens"),
        fn.COALESCE(fn.SUM(SQL("COALESCE((usage->>'batch_tokens')::bigint, 0)")), 0).alias("sum_batch_tokens"),
    ).group_by(
        fn.DATE(db_models.ModelUsage.created_at),
        db_models.ModelUsage.model_name,
        db_models.ModelUsage.model_type,
    ).order_by(
        fn.DATE(db_models.ModelUsage.created_at).asc()
    )
    
    agg_query = apply_conditions(agg_query)
    agg_result = await db_manager.execute_query(agg_query)
    rows = list(agg_result)

    # 일별 집계
    daily_map: dict[str, dict] = defaultdict(lambda: {
        "total_requests": 0, "success_count": 0, "error_count": 0,
        "estimated_cost": 0.0,
    })
    
    # 모델별 집계
    model_map: dict[str, dict] = defaultdict(lambda: {
        "model_type": "", "total_requests": 0, "success_count": 0,
        "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
        "single_tokens": 0, "batch_tokens": 0,
        "estimated_cost": 0.0,
    })
    
    # overview 집계
    overview_total_requests = 0
    overview_success_count = 0
    overview_estimated_cost = 0.0

    for row in rows:
        date_str = str(row.date)
        model_name = row.model_name
        m_type = row.model_type
        total_req = row.total_requests
        success_cnt = row.success_count
        error_cnt = row.error_count

        usage = {}
        if m_type == ModelType.TEXT_GENERATION.value:
            usage = {
                "input_tokens": int(row.sum_input_tokens),
                "cached_input_tokens": int(row.sum_cached_input_tokens),
                "output_tokens": int(row.sum_output_tokens),
            }
        elif m_type == ModelType.EMBEDDING.value:
            usage = {
                "single_tokens": int(row.sum_single_tokens),
                "batch_tokens": int(row.sum_batch_tokens),
            }

        cost_info = model_cost_map.get(model_name)
        row_cost = _calculate_cost(m_type, usage, cost_info, total_req)

        # 일별
        d = daily_map[date_str]
        d["total_requests"] += total_req
        d["success_count"] += success_cnt
        d["error_count"] += error_cnt
        d["estimated_cost"] += row_cost

        # 모델별
        mm = model_map[model_name]
        mm["model_type"] = m_type
        mm["total_requests"] += total_req
        mm["success_count"] += success_cnt
        mm["input_tokens"] += int(row.sum_input_tokens)
        mm["cached_input_tokens"] += int(row.sum_cached_input_tokens)
        mm["output_tokens"] += int(row.sum_output_tokens)
        mm["single_tokens"] += int(row.sum_single_tokens)
        mm["batch_tokens"] += int(row.sum_batch_tokens)
        mm["estimated_cost"] += row_cost

        # overview
        overview_total_requests += total_req
        overview_success_count += success_cnt
        overview_estimated_cost += row_cost

    # 지난달 집계 (MoM 비교용)
    prev_conditions = [
        db_models.ModelUsage.created_at >= prev_start,
        db_models.ModelUsage.created_at <= prev_end,
    ]
    if target_user_name:
        prev_conditions.append(db_models.ModelUsage.user_name == target_user_name)

    prev_query = db_models.ModelUsage.select(
        db_models.ModelUsage.model_name,
        db_models.ModelUsage.model_type,
        fn.COUNT(db_models.ModelUsage.id).alias("total_requests"),
        fn.COUNT(db_models.ModelUsage.id).filter(
            db_models.ModelUsage.status == ModelUsageStatus.SUCCESS.value
        ).alias("success_count"),
        fn.COALESCE(fn.SUM(SQL("COALESCE((usage->>'input_tokens')::bigint, 0)")), 0).alias("sum_input_tokens"),
        fn.COALESCE(fn.SUM(SQL("COALESCE((usage->>'cached_input_tokens')::bigint, 0)")), 0).alias("sum_cached_input_tokens"),
        fn.COALESCE(fn.SUM(SQL("COALESCE((usage->>'output_tokens')::bigint, 0)")), 0).alias("sum_output_tokens"),
        fn.COALESCE(fn.SUM(SQL("COALESCE((usage->>'single_tokens')::bigint, 0)")), 0).alias("sum_single_tokens"),
        fn.COALESCE(fn.SUM(SQL("COALESCE((usage->>'batch_tokens')::bigint, 0)")), 0).alias("sum_batch_tokens"),
    ).group_by(
        db_models.ModelUsage.model_name,
        db_models.ModelUsage.model_type,
    )
    for cond in prev_conditions:
        prev_query = prev_query.where(cond)
    prev_result = await db_manager.execute_query(prev_query)

    prev_total_requests = 0
    prev_success_count = 0
    prev_estimated_cost = 0.0
    for row in prev_result:
        m_type = row.model_type
        usage = {}
        if m_type == ModelType.TEXT_GENERATION.value:
            usage = {
                "input_tokens": int(row.sum_input_tokens),
                "cached_input_tokens": int(row.sum_cached_input_tokens),
                "output_tokens": int(row.sum_output_tokens),
            }
        elif m_type == ModelType.EMBEDDING.value:
            usage = {
                "single_tokens": int(row.sum_single_tokens),
                "batch_tokens": int(row.sum_batch_tokens),
            }
        cost_info = model_cost_map.get(row.model_name)
        prev_total_requests += row.total_requests
        prev_success_count += row.success_count
        prev_estimated_cost += _calculate_cost(m_type, usage, cost_info, row.total_requests)

    prev_success_rate = (
        (prev_success_count / prev_total_requests * 100)
        if prev_total_requests > 0 else 0
    )

    def _pct_change(current: float, previous: float) -> float:
        """퍼센트 변동률을 계산합니다.
        
        Args:
            current (float): 현재 값
            previous (float): 이전 값

        Returns:
            float: 퍼센트 변동률
        """
        
        if previous == 0:
            return 0.0
        return round((current - previous) / previous * 100, 2)

    # 활성 모델 수
    active_models_query = db_models.Model.select(
        fn.COUNT(db_models.Model.name).alias("count")
    ).where(db_models.Model.status == ModelStatus.RUNNING.value)
    active_models_result = await db_manager.execute_query(active_models_query)
    active_models_row = list(active_models_result)[0] if active_models_result else None

    overview_success_rate = (
        (overview_success_count / overview_total_requests * 100)
        if overview_total_requests > 0 else 0
    )
    mom_change = MonthOverMonthChange(
        total_requests=_pct_change(overview_total_requests, prev_total_requests),
        estimated_cost=_pct_change(overview_estimated_cost, prev_estimated_cost),
        success_rate=round(overview_success_rate - prev_success_rate, 2),
    )

    overview = OverviewStats(
        total_requests=overview_total_requests,
        success_rate=round(overview_success_rate, 2),
        estimated_cost=round(overview_estimated_cost, 6),
        active_models=active_models_row.count if active_models_row else 0,
        mom_change=mom_change,
    )

    # 일별 사용량
    daily_usage = []
    for date_str in sorted(daily_map.keys()):
        d = daily_map[date_str]
        daily_usage.append(DailyUsage(
            date=date_str,
            total_requests=d["total_requests"],
            success_count=d["success_count"],
            error_count=d["error_count"],
            estimated_cost=round(d["estimated_cost"], 6),
        ))

    # 모델별 사용량
    model_usage = []
    for model_name, mm in sorted(model_map.items(), key=lambda x: x[1]["estimated_cost"], reverse=True):
        m_type = mm["model_type"]
        success_rate = (
            (mm["success_count"] / mm["total_requests"] * 100)
            if mm["total_requests"] > 0 else 0
        )

        if m_type == ModelType.TEXT_GENERATION.value:
            usage_details = TextGenerationUsage(
                input_tokens=mm["input_tokens"],
                cached_input_tokens=mm["cached_input_tokens"],
                output_tokens=mm["output_tokens"],
            )
        elif m_type == ModelType.EMBEDDING.value:
            usage_details = EmbeddingUsage(
                single_tokens=mm["single_tokens"],
                batch_tokens=mm["batch_tokens"],
            )
        else:
            usage_details = RerankUsage()

        cost_info = model_cost_map.get(model_name)

        model_usage.append(ModelUsageStats(
            model_name=model_name,
            model_type=m_type,
            total_requests=mm["total_requests"],
            success_rate=round(success_rate, 2),
            usage_details=usage_details,
            cost_info=cost_info,
            estimated_cost=round(mm["estimated_cost"], 6),
        ))

    # 사용자별 사용량 (관리자만)
    user_usage = None
    if is_admin and not user_name:
        user_agg_query = db_models.ModelUsage.select(
            db_models.ModelUsage.user_name,
            db_models.ModelUsage.model_name,
            db_models.ModelUsage.model_type,
            fn.COUNT(db_models.ModelUsage.id).alias("total_requests"),
            fn.COALESCE(fn.SUM(SQL("COALESCE((usage->>'input_tokens')::bigint, 0)")), 0).alias("sum_input_tokens"),
            fn.COALESCE(fn.SUM(SQL("COALESCE((usage->>'cached_input_tokens')::bigint, 0)")), 0).alias("sum_cached_input_tokens"),
            fn.COALESCE(fn.SUM(SQL("COALESCE((usage->>'output_tokens')::bigint, 0)")), 0).alias("sum_output_tokens"),
            fn.COALESCE(fn.SUM(SQL("COALESCE((usage->>'single_tokens')::bigint, 0)")), 0).alias("sum_single_tokens"),
            fn.COALESCE(fn.SUM(SQL("COALESCE((usage->>'batch_tokens')::bigint, 0)")), 0).alias("sum_batch_tokens"),
        ).group_by(
            db_models.ModelUsage.user_name,
            db_models.ModelUsage.model_name,
            db_models.ModelUsage.model_type,
        )
        user_agg_query = apply_conditions(user_agg_query)
        user_agg_result = await db_manager.execute_query(user_agg_query)

        user_cost_map: dict[str, dict] = defaultdict(lambda: {
            "total_requests": 0, "estimated_cost": 0.0,
        })
        for row in user_agg_result:
            uid = row.user_name
            m_type = row.model_type
            usage = {}
            if m_type == ModelType.TEXT_GENERATION.value:
                usage = {
                    "input_tokens": int(row.sum_input_tokens),
                    "cached_input_tokens": int(row.sum_cached_input_tokens),
                    "output_tokens": int(row.sum_output_tokens),
                }
            elif m_type == ModelType.EMBEDDING.value:
                usage = {
                    "single_tokens": int(row.sum_single_tokens),
                    "batch_tokens": int(row.sum_batch_tokens),
                }
            
            cost_info = model_cost_map.get(row.model_name)
            row_cost = _calculate_cost(m_type, usage, cost_info, row.total_requests)

            user_cost_map[uid]["total_requests"] += row.total_requests
            user_cost_map[uid]["estimated_cost"] += row_cost

        user_usage = sorted(
            [
                UserUsageStats(
                    user_name=uid,
                    total_requests=data["total_requests"],
                    estimated_cost=round(data["estimated_cost"], 6),
                )
                for uid, data in user_cost_map.items()
            ],
            key=lambda x: x.estimated_cost,
            reverse=True,
        )[:20]

    return DashboardStatsResponse(
        month=month_str,
        overview=overview,
        daily_usage=daily_usage,
        model_usage=model_usage,
        user_usage=user_usage,
    )
