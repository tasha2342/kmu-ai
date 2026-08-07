import uuid

from collections import defaultdict

from logging import Logger

from datetime import date, datetime, time, timedelta

from typing import Optional
from uuid import UUID

from peewee import fn, SQL, JOIN

from fastapi import APIRouter, Depends, Path, Query

from app.config import config
from app.exceptions.api_exception import (
    DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
)
from app.payloads.v1.chatbot_admin import (
    UpdateUnansweredQuestionPayload,
    AddUserFeedbackPayload,
)
from app.responses.base import BaseMessageResponse, BaseMessageIdResponse, BaseListResponse
from app.responses.exception import (
    BadRequestResponse,
    ForbiddenResponse,
    NotFoundResponse,
)
from app.responses.v1.chatbot_admin import (
    DailyQuestionTrend,
    ChatbotStatsResponse,
    KeywordStat,
    KeywordStatsResponse,
    IntentStat,
    IntentStatsResponse,
    FeedbackRatingBucket,
    DailyRatingTrend,
    FeedbackStatsResponse,
    UserFeedbackListItem,
    ConversationLogItem,
    UserUsageLogItem,
)
from app.models.api.exception import (
    BadRequestError,
    NotFoundError,
)
from app.models.auth import TokenUserInfo
from app.models.enum import (
    ChatIntent,
    ChatRole,
    ReviewStatus,
    UnansweredReason,
)
from app.utils.logger import get_api_logger
from app.utils.database import DatabaseManager, get_db_manager
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.auth as auth
import app.utils.common as util


router = APIRouter()


DEFAULT_PERIOD_DAYS = 30
"""기간 미지정 시 조회할 기본 일수"""

INVALID_PERIOD_RESPONSE = {
    "description": "잘못된 요청입니다.",
    "model": BadRequestError,
    "content": {
        "application/json": {
            "examples": {
                "invalid_period": {
                    "summary": "시작일이 종료일보다 늦습니다.",
                    "value": {
                        "message": "시작일이 종료일보다 늦을 수 없습니다.",
                        "target": "start_date={start_date}, end_date={end_date}"
                    }
                }
            }
        }
    }
}
"""기간 조건이 올바르지 않은 경우의 응답 정의"""

# 인기 키워드 집계용 SQL 조각
# 1) 공백 기준으로 토큰을 분리하고, 2) 영문·숫자·한글 외 문자를 제거한 뒤,
# 3) 3글자 이상인 토큰에 한해 말미의 조사를 제거합니다.
#
# `요`/`과`/`로` 등은 조사가 아닌 어미·단어 끝 글자로 쓰이는 경우가 많아 제외했습니다.
# (`알려주세요` → `알려주세`, `국어국문학과` → `국어국문학` 과 같은 오절단 방지)
_KEYWORD_JOSA_PATTERN = "|".join([
    "에서부터", "으로부터", "에게서", "한테서", "이라고", "이라는", "으로서", "으로써",
    "에서는", "에게는", "에서도", "에게도", "까지는", "부터는", "께서는",
    "라고", "라는", "까지", "부터", "에서", "에게", "한테", "처럼", "보다", "으로",
    "이나", "이란", "이든", "든지", "밖에", "조차", "마저", "마다", "께서", "이라",
    "은", "는", "이", "가", "을", "를", "의", "에",
])
_KEYWORD_TOKEN_SQL = (
    "unnest(regexp_split_to_array(lower(query_text), '[[:space:]]+')) AS raw_token"
)
_KEYWORD_BASE_SQL = "regexp_replace(t.raw_token, '[^[:alnum:]]+', '', 'g')"
_KEYWORD_NORMALIZE_SQL = (
    f"CASE WHEN char_length({_KEYWORD_BASE_SQL}) >= 3 "
    f"THEN regexp_replace({_KEYWORD_BASE_SQL}, '({_KEYWORD_JOSA_PATTERN})$', '') "
    f"ELSE {_KEYWORD_BASE_SQL} END AS keyword"
)
_KEYWORD_STOPWORDS = [
    # 지시어·의문사
    "그것", "이것", "저것", "무엇", "어디", "언제", "어떤", "무슨", "어떻게", "얼마",
    # 접속어
    "그리고", "그러나", "하지만", "그런데", "그래서", "또는", "그럼", "만약",
    # 상용 표현
    "있나요", "있는지", "있습니다", "있어요", "없나요", "없는지", "됩니다", "되나요",
    "인가요", "입니다", "합니다", "하나요", "해주세요", "알려주세요", "알려줘",
    "궁금합니다", "궁금해요", "부탁드립니다", "안녕하세요", "감사합니다",
    "언제인가요", "무엇인가요", "어디인가요", "가능한가요", "있을까요", "해야하나요",
    "대해", "대한", "관련", "위해", "하는", "하고", "해서", "때문", "경우", "가능",
    "저는", "제가", "우리", "정말", "진짜", "혹시", "다시", "너무", "좀더",
    # 영문 불용어
    "the", "and", "for", "you", "are", "how", "what", "when", "where", "which",
    "that", "this", "with", "can", "please", "tell", "about",
]
"""인기 키워드 집계에서 제외할 불용어 목록"""


def _validate_period(start_date: Optional[date], end_date: Optional[date]) -> Optional[BadRequestResponse]:
    """조회 기간 조건이 올바른지 검사합니다.

    Args:
        start_date (Optional[date]): 조회 시작일
        end_date (Optional[date]): 조회 종료일

    Returns:
        Optional[BadRequestResponse]: 조건이 올바르지 않은 경우의 오류 응답 (정상이면 None)
    """

    if start_date and end_date and start_date > end_date:
        return BadRequestResponse(
            message="시작일이 종료일보다 늦을 수 없습니다.",
            target=f"start_date={start_date}, end_date={end_date}"
        )
    return None

def _resolve_period(start_date: Optional[date], end_date: Optional[date]) -> tuple[datetime, datetime]:
    """조회 기간을 일시 범위로 변환합니다.

    기간이 지정되지 않으면 종료일은 오늘, 시작일은 종료일로부터 `DEFAULT_PERIOD_DAYS`일 전이 됩니다.

    Args:
        start_date (Optional[date]): 조회 시작일
        end_date (Optional[date]): 조회 종료일

    Returns:
        tuple[datetime, datetime]: 조회 시작 일시와 종료 일시
    """

    now = util.get_now()
    resolved_end = end_date or now.date()
    resolved_start = start_date or (resolved_end - timedelta(days=DEFAULT_PERIOD_DAYS - 1))

    period_start = datetime.combine(resolved_start, time.min, tzinfo=now.tzinfo)
    period_end = datetime.combine(resolved_end, time.max, tzinfo=now.tzinfo)

    return period_start, period_end

def _combine_conditions(conditions: list):
    """조건 목록을 AND로 결합합니다.

    Args:
        conditions (list): 결합할 조건 목록

    Returns:
        결합된 조건 (조건이 없으면 None)
    """

    if not conditions:
        return None

    combined_condition = conditions[0]
    for condition in conditions[1:]:
        combined_condition = combined_condition & condition
    return combined_condition

def _calculate_total_pages(total_count: int, count: int) -> int:
    """총 페이지 수를 계산합니다.

    Args:
        total_count (int): 총 항목 수
        count (int): 페이지당 항목 수 (0이면 전체 조회)

    Returns:
        int: 총 페이지 수
    """

    if count > 0:
        return (total_count + count - 1) // count if total_count > 0 else 1
    return 1

def _to_float(value, default: Optional[float] = None) -> Optional[float]:
    """집계 결과 값을 실수로 변환합니다.

    Args:
        value: 변환할 값 (Decimal, int, float, None)
        default (Optional[float]): 값이 없는 경우 반환할 기본값

    Returns:
        Optional[float]: 변환된 실수 값
    """

    if value is None:
        return default
    return float(value)


@router.get("/stats", summary="챗봇 이용 통계 조회",
    description=(
        "기간별 챗봇 이용 통계를 조회합니다. (KAI-REQ-031)  \n"
        "- 총 질문 수: 사용자(user) 메시지 수  \n"
        "- 총 세션 수/활성 사용자 수: 기간 내 생성된 세션과 해당 세션의 고유 사용자 수  \n"
        "- 평균 응답시간: 챗봇(assistant) 메시지의 `latency_ms` 평균 (0인 값은 제외)  \n"
        "- 미응답 건수/미응답률: 미응답 질문 수와 총 질문 수 대비 비율  \n"
        "- 평균 만족도: 사용자 피드백 평점(1~5) 평균  \n"
        "- 일자별 질문 추이: 질문이 발생한 일자만 반환됩니다.  \n"
        "- 기본 조회 기간: 최근 30일"
    ),
    responses={
        200: {"description": "챗봇 이용 통계를 반환합니다.", "model": ChatbotStatsResponse},
        400: INVALID_PERIOD_RESPONSE,
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_chatbot_stats(
    start_date: Optional[date] = Query(
        None,
        description="조회 시작일입니다. (YYYY-MM-DD, 기본값: 종료일 기준 29일 전)",
        examples=["2026-07-01"]
    ),
    end_date: Optional[date] = Query(
        None,
        description="조회 종료일입니다. (YYYY-MM-DD, 기본값: 오늘)",
        examples=["2026-07-27"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    error_response = _validate_period(start_date, end_date)
    if error_response:
        return error_response

    period_start, period_end = _resolve_period(start_date, end_date)

    # 질문 수 / 평균 응답시간 집계
    message_query = db_models.ChatMessage.select(
        fn.COUNT(db_models.ChatMessage.id).filter(
            db_models.ChatMessage.role == ChatRole.USER.value
        ).alias("question_count"),
        fn.AVG(db_models.ChatMessage.latency_ms).filter(
            (db_models.ChatMessage.role == ChatRole.ASSISTANT.value) &
            (db_models.ChatMessage.latency_ms > 0)
        ).alias("avg_latency_ms"),
    ).where(
        (db_models.ChatMessage.created_at >= period_start) &
        (db_models.ChatMessage.created_at <= period_end)
    )
    message_result = await db_manager.execute_query(message_query)
    message_row = list(message_result)[0] if message_result else None

    total_questions = message_row.question_count if message_row else 0
    avg_response_time_ms = _to_float(message_row.avg_latency_ms, 0.0) if message_row else 0.0

    # 세션 수 / 활성 사용자 수 집계
    session_query = db_models.ChatSession.select(
        fn.COUNT(db_models.ChatSession.id).alias("session_count"),
        fn.COUNT(fn.DISTINCT(db_models.ChatSession.user_name)).alias("active_users"),
    ).where(
        (db_models.ChatSession.created_at >= period_start) &
        (db_models.ChatSession.created_at <= period_end)
    )
    session_result = await db_manager.execute_query(session_query)
    session_row = list(session_result)[0] if session_result else None

    total_sessions = session_row.session_count if session_row else 0
    active_users = session_row.active_users if session_row else 0

    # 미응답 건수 집계
    unanswered_query = db_models.UnansweredQuestion.select(
        fn.COUNT(db_models.UnansweredQuestion.id).alias("count")
    ).where(
        (db_models.UnansweredQuestion.created_at >= period_start) &
        (db_models.UnansweredQuestion.created_at <= period_end)
    )
    unanswered_result = await db_manager.execute_query(unanswered_query)
    unanswered_row = list(unanswered_result)[0] if unanswered_result else None
    unanswered_count = unanswered_row.count if unanswered_row else 0

    # 만족도 집계
    feedback_query = db_models.UserFeedback.select(
        fn.COUNT(db_models.UserFeedback.id).alias("count"),
        fn.AVG(db_models.UserFeedback.rating).alias("average_rating"),
    ).where(
        (db_models.UserFeedback.created_at >= period_start) &
        (db_models.UserFeedback.created_at <= period_end)
    )
    feedback_result = await db_manager.execute_query(feedback_query)
    feedback_row = list(feedback_result)[0] if feedback_result else None

    feedback_count = feedback_row.count if feedback_row else 0
    average_rating = _to_float(feedback_row.average_rating) if feedback_row else None

    # 일자별 질문 추이 집계
    daily_question_query = db_models.ChatMessage.select(
        fn.DATE(db_models.ChatMessage.created_at).alias("date"),
        fn.COUNT(db_models.ChatMessage.id).alias("count"),
    ).where(
        (db_models.ChatMessage.role == ChatRole.USER.value) &
        (db_models.ChatMessage.created_at >= period_start) &
        (db_models.ChatMessage.created_at <= period_end)
    ).group_by(
        fn.DATE(db_models.ChatMessage.created_at)
    )
    daily_question_result = await db_manager.execute_query(daily_question_query)

    # 일자별 미응답 추이 집계
    daily_unanswered_query = db_models.UnansweredQuestion.select(
        fn.DATE(db_models.UnansweredQuestion.created_at).alias("date"),
        fn.COUNT(db_models.UnansweredQuestion.id).alias("count"),
    ).where(
        (db_models.UnansweredQuestion.created_at >= period_start) &
        (db_models.UnansweredQuestion.created_at <= period_end)
    ).group_by(
        fn.DATE(db_models.UnansweredQuestion.created_at)
    )
    daily_unanswered_result = await db_manager.execute_query(daily_unanswered_query)

    daily_map: dict[str, dict] = defaultdict(lambda: {"question_count": 0, "unanswered_count": 0})
    for row in daily_question_result:
        daily_map[str(row.date)]["question_count"] = row.count
    for row in daily_unanswered_result:
        daily_map[str(row.date)]["unanswered_count"] = row.count

    daily_trend = [
        DailyQuestionTrend(
            date=date_str,
            question_count=values["question_count"],
            unanswered_count=values["unanswered_count"],
        )
        for date_str, values in sorted(daily_map.items())
    ]

    unanswered_rate = (unanswered_count / total_questions * 100) if total_questions > 0 else 0.0

    return ChatbotStatsResponse(
        start_date=period_start.date().isoformat(),
        end_date=period_end.date().isoformat(),
        total_questions=total_questions,
        total_sessions=total_sessions,
        active_users=active_users,
        avg_response_time_ms=round(avg_response_time_ms, 2),
        unanswered_count=unanswered_count,
        unanswered_rate=round(unanswered_rate, 2),
        feedback_count=feedback_count,
        average_rating=round(average_rating, 2) if average_rating is not None else None,
        daily_trend=daily_trend,
    )

@router.get("/stats/keywords", summary="인기 키워드 통계 조회",
    description=(
        "기간별 인기 키워드를 출현 횟수 기준으로 조회합니다. (KAI-REQ-031)  \n"
        "검색 로그(`retrieval_logs`)의 질문 텍스트를 공백으로 토큰화하여 집계합니다.  \n"
        "  \n"
        "**집계 방식의 한계**  \n"
        "- 외부 형태소 분석기를 사용하지 않는 단순 규칙 기반 집계입니다.  \n"
        "- 공백 기준 분리이므로 띄어쓰기가 없는 복합어는 하나의 키워드로 집계됩니다.  \n"
        "- 조사는 3글자 이상 토큰의 말미에서만 제거되므로, 2글자 단어(예: `학과`)가 잘못 잘리지는 않지만 일부 조사는 남을 수 있습니다.  \n"
        "- 어간 추출을 하지 않으므로 활용형(`신청`/`신청하는`)은 서로 다른 키워드로 집계됩니다.  \n"
        "- 조사 제거 규칙이 실제 단어의 끝 글자와 겹치면 과도하게 잘릴 수 있습니다. (예: `학생회의` → `학생회`)  \n"
        "- 오절단이 잦은 `요`·`과`·`로`는 조사 제거 대상에서 제외했으므로, 해당 조사는 키워드에 그대로 남습니다.  \n"
        "- 한 글자 토큰과 불용어 목록에 포함된 토큰은 집계에서 제외됩니다.  \n"
        "- 기본 조회 기간: 최근 30일"
    ),
    responses={
        200: {"description": "인기 키워드 통계를 반환합니다.", "model": KeywordStatsResponse},
        400: INVALID_PERIOD_RESPONSE,
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_keyword_stats(
    start_date: Optional[date] = Query(
        None,
        description="조회 시작일입니다. (YYYY-MM-DD, 기본값: 종료일 기준 29일 전)",
        examples=["2026-07-01"]
    ),
    end_date: Optional[date] = Query(
        None,
        description="조회 종료일입니다. (YYYY-MM-DD, 기본값: 오늘)",
        examples=["2026-07-27"]
    ),
    top_n: int = Query(
        20, ge=1, le=100,
        description="조회할 상위 키워드 수입니다. (최대 100개)",
        examples=[10, 20, 50]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    error_response = _validate_period(start_date, end_date)
    if error_response:
        return error_response

    period_start, period_end = _resolve_period(start_date, end_date)

    period_condition = (
        (db_models.RetrievalLog.created_at >= period_start) &
        (db_models.RetrievalLog.created_at <= period_end)
    )

    # 집계 대상 질의 수 조회
    total_query = db_models.RetrievalLog.select(
        fn.COUNT(db_models.RetrievalLog.id).alias("count")
    ).where(period_condition)
    total_result = await db_manager.execute_query(total_query)
    total_row = list(total_result)[0] if total_result else None
    total_queries = total_row.count if total_row else 0

    # 1단계: 질문 텍스트를 공백 기준으로 토큰화
    raw_token_query = (db_models.RetrievalLog
                        .select(SQL(_KEYWORD_TOKEN_SQL))
                        .where(period_condition))

    # 2단계: 특수문자 제거 및 조사 제거로 키워드 정규화
    normalized_query = (db_models.RetrievalLog
                        .select(SQL(_KEYWORD_NORMALIZE_SQL))
                        .from_(raw_token_query.alias("t")))

    # 3단계: 불용어·한 글자 토큰 제외 후 출현 횟수 집계
    keyword_query = (db_models.RetrievalLog
                        .select(
                            SQL("k.keyword"),
                            fn.COUNT(SQL("1")).alias("count"),
                        )
                        .from_(normalized_query.alias("k"))
                        .where(
                            (SQL("char_length(k.keyword)") > 1) &
                            (SQL("k.keyword").not_in(_KEYWORD_STOPWORDS))
                        )
                        .group_by(SQL("k.keyword"))
                        .order_by(fn.COUNT(SQL("1")).desc(), SQL("k.keyword").asc())
                        .limit(top_n)
                        .dicts())
    keyword_result = await db_manager.execute_query(keyword_query)

    keywords = [
        KeywordStat(keyword=row["keyword"], count=row["count"])
        for row in keyword_result
    ]

    return KeywordStatsResponse(
        start_date=period_start.date().isoformat(),
        end_date=period_end.date().isoformat(),
        total_queries=total_queries,
        keywords=keywords,
    )

@router.get("/stats/intents", summary="의도별 질문 분포 조회",
    description=(
        "기간별 의도(intent) 분포를 조회합니다. (KAI-REQ-031)  \n"
        "대화 메시지의 `detected_intent`를 기준으로 집계합니다.  \n"
        "  \n"
        "의도는 메시지 단위로 기록되므로 질문과 응답 양쪽에 기록되는 경우 중복 집계될 수 있습니다.  \n"
        "기본값은 사용자(user) 메시지 기준이며, `role`로 집계 대상을 변경할 수 있습니다.  \n"
        "기본 조회 기간: 최근 30일"
    ),
    responses={
        200: {"description": "의도별 질문 분포를 반환합니다.", "model": IntentStatsResponse},
        400: INVALID_PERIOD_RESPONSE,
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_intent_stats(
    start_date: Optional[date] = Query(
        None,
        description="조회 시작일입니다. (YYYY-MM-DD, 기본값: 종료일 기준 29일 전)",
        examples=["2026-07-01"]
    ),
    end_date: Optional[date] = Query(
        None,
        description="조회 종료일입니다. (YYYY-MM-DD, 기본값: 오늘)",
        examples=["2026-07-27"]
    ),
    role: Optional[ChatRole] = Query(
        ChatRole.USER,
        description="집계 대상 메시지 역할입니다. (미지정 시 전체 메시지)",
        examples=list(ChatRole)
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    error_response = _validate_period(start_date, end_date)
    if error_response:
        return error_response

    period_start, period_end = _resolve_period(start_date, end_date)

    conditions = [
        db_models.ChatMessage.created_at >= period_start,
        db_models.ChatMessage.created_at <= period_end,
    ]
    if role is not None:
        conditions.append(db_models.ChatMessage.role == role.value)

    intent_query = (db_models.ChatMessage
                        .select(
                            db_models.ChatMessage.detected_intent,
                            fn.COUNT(db_models.ChatMessage.id).alias("count"),
                        )
                        .where(_combine_conditions(conditions))
                        .group_by(db_models.ChatMessage.detected_intent))
    intent_result = await db_manager.execute_query(intent_query)

    intent_counts: dict[str, int] = {}
    undetected_count = 0
    for row in intent_result:
        if row.detected_intent is None:
            undetected_count += row.count
            continue
        intent_value = row.detected_intent.value if isinstance(row.detected_intent, ChatIntent) else str(row.detected_intent)
        intent_counts[intent_value] = intent_counts.get(intent_value, 0) + row.count

    total_count = sum(intent_counts.values())

    intents = sorted(
        [
            IntentStat(
                intent=intent,
                count=intent_counts.get(intent.value, 0),
                ratio=round(intent_counts.get(intent.value, 0) / total_count * 100, 2) if total_count > 0 else 0.0,
            )
            for intent in ChatIntent
        ],
        key=lambda item: item.count,
        reverse=True,
    )

    return IntentStatsResponse(
        start_date=period_start.date().isoformat(),
        end_date=period_end.date().isoformat(),
        total_count=total_count,
        undetected_count=undetected_count,
        intents=intents,
    )

@router.get("/stats/feedback", summary="만족도 통계 조회",
    description=(
        "기간별 사용자 만족도 통계를 조회합니다. (KAI-REQ-033)  \n"
        "- 평점 분포: 1~5점 히스토그램 (건수가 없는 평점도 0건으로 반환)  \n"
        "- 평균 만족도: 평가가 없으면 null  \n"
        "- 일자별 추이: 평가가 있는 일자만 반환됩니다.  \n"
        "- 기본 조회 기간: 최근 30일"
    ),
    responses={
        200: {"description": "만족도 통계를 반환합니다.", "model": FeedbackStatsResponse},
        400: INVALID_PERIOD_RESPONSE,
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_feedback_stats(
    start_date: Optional[date] = Query(
        None,
        description="조회 시작일입니다. (YYYY-MM-DD, 기본값: 종료일 기준 29일 전)",
        examples=["2026-07-01"]
    ),
    end_date: Optional[date] = Query(
        None,
        description="조회 종료일입니다. (YYYY-MM-DD, 기본값: 오늘)",
        examples=["2026-07-27"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    error_response = _validate_period(start_date, end_date)
    if error_response:
        return error_response

    period_start, period_end = _resolve_period(start_date, end_date)

    period_condition = (
        (db_models.UserFeedback.created_at >= period_start) &
        (db_models.UserFeedback.created_at <= period_end)
    )

    # 평점 분포 집계
    distribution_query = (db_models.UserFeedback
                            .select(
                                db_models.UserFeedback.rating,
                                fn.COUNT(db_models.UserFeedback.id).alias("count"),
                            )
                            .where(period_condition)
                            .group_by(db_models.UserFeedback.rating))
    distribution_result = await db_manager.execute_query(distribution_query)

    rating_counts: dict[int, int] = {}
    for row in distribution_result:
        rating_counts[int(row.rating)] = row.count

    total_count = sum(rating_counts.values())
    average_rating = None
    if total_count > 0:
        average_rating = sum(rating * count for rating, count in rating_counts.items()) / total_count

    distribution = [
        FeedbackRatingBucket(
            rating=rating,
            count=rating_counts.get(rating, 0),
            ratio=round(rating_counts.get(rating, 0) / total_count * 100, 2) if total_count > 0 else 0.0,
        )
        for rating in range(1, 6)
    ]

    # 일자별 만족도 추이 집계
    trend_query = (db_models.UserFeedback
                    .select(
                        fn.DATE(db_models.UserFeedback.created_at).alias("date"),
                        fn.COUNT(db_models.UserFeedback.id).alias("count"),
                        fn.AVG(db_models.UserFeedback.rating).alias("average_rating"),
                    )
                    .where(period_condition)
                    .group_by(fn.DATE(db_models.UserFeedback.created_at))
                    .order_by(fn.DATE(db_models.UserFeedback.created_at).asc()))
    trend_result = await db_manager.execute_query(trend_query)

    daily_trend = [
        DailyRatingTrend(
            date=str(row.date),
            count=row.count,
            average_rating=round(_to_float(row.average_rating, 0.0), 2),
        )
        for row in trend_result
    ]

    return FeedbackStatsResponse(
        start_date=period_start.date().isoformat(),
        end_date=period_end.date().isoformat(),
        total_count=total_count,
        average_rating=round(average_rating, 2) if average_rating is not None else None,
        distribution=distribution,
        daily_trend=daily_trend,
    )

@router.get("/unanswered/list", summary="미응답 질문 목록 조회",
    description=(
        "미응답 질문 목록을 페이징 처리하여 조회합니다. (KAI-REQ-031/040)  \n"
        "미응답 사유와 검토 상태, 기간으로 필터링할 수 있으며 최신순으로 정렬됩니다."
    ),
    responses={
        200: {"description": "미응답 질문 목록을 반환합니다.", "model": BaseListResponse[db_items.UnansweredQuestion]},
        400: INVALID_PERIOD_RESPONSE,
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_unanswered_list(
    page: int = Query(
        1, ge=1,
        description="조회할 페이지 번호입니다.",
        examples=[1, 2, 3, 4, 5]
    ),
    count: int = Query(
        10, ge=0, le=100,
        description="페이지당 항목 수입니다. (최대 100개, 0이면 전체 조회)",
        examples=[10, 20, 50, 100]
    ),
    reason: Optional[UnansweredReason] = Query(
        None,
        description="미응답 사유로 필터링합니다.",
        examples=list(UnansweredReason)
    ),
    review_status: Optional[ReviewStatus] = Query(
        None,
        description="검토 상태로 필터링합니다.",
        examples=list(ReviewStatus)
    ),
    start_date: Optional[date] = Query(
        None,
        description="조회 시작일입니다. (YYYY-MM-DD, 미지정 시 제한 없음)",
        examples=["2026-07-01"]
    ),
    end_date: Optional[date] = Query(
        None,
        description="조회 종료일입니다. (YYYY-MM-DD, 미지정 시 제한 없음)",
        examples=["2026-07-27"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    error_response = _validate_period(start_date, end_date)
    if error_response:
        return error_response

    # 필터 조건 추가
    conditions = []
    if reason is not None:
        conditions.append(db_models.UnansweredQuestion.reason == reason.value)
    if review_status is not None:
        conditions.append(db_models.UnansweredQuestion.review_status == review_status.value)
    if start_date is not None:
        period_start, _ = _resolve_period(start_date, start_date)
        conditions.append(db_models.UnansweredQuestion.created_at >= period_start)
    if end_date is not None:
        _, period_end = _resolve_period(end_date, end_date)
        conditions.append(db_models.UnansweredQuestion.created_at <= period_end)

    query = db_models.UnansweredQuestion.select()
    count_query = db_models.UnansweredQuestion.select(
        fn.COUNT(db_models.UnansweredQuestion.id).alias("count")
    )

    combined_condition = _combine_conditions(conditions)
    if combined_condition is not None:
        query = query.where(combined_condition)
        count_query = count_query.where(combined_condition)

    query = query.order_by(db_models.UnansweredQuestion.created_at.desc())

    # 페이징 처리 (count가 0이면 전체 조회)
    if count > 0:
        offset = (page - 1) * count
        query = query.offset(offset).limit(count)

    # 총 개수 조회
    count_result = await db_manager.execute_query(count_query)
    total_count = count_result[0].count if count_result else 0

    # 목록 조회
    items: list[db_items.UnansweredQuestion] = await db_manager.select_items(query)

    return BaseListResponse[db_items.UnansweredQuestion](
        total_pages=_calculate_total_pages(total_count, count),
        total_count=total_count,
        items=items
    )

@router.patch("/unanswered/{unanswered_id}", summary="미응답 질문 검토 상태 변경",
    description=(
        "미응답 질문의 검토 상태를 변경합니다. (KAI-REQ-031/040)  \n"
        "검토자는 요청자의 사용자명으로, 검토 일시는 요청 시각으로 기록됩니다."
    ),
    responses={
        200: {"description": "검토 상태가 변경되었습니다.", "model": BaseMessageResponse},
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 미응답 질문",
                            "value": {
                                "message": "등록되지 않은 미응답 질문입니다.",
                                "target": "unanswered_id={unanswered_id}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def update_unanswered_question(
    payload: UpdateUnansweredQuestionPayload,
    unanswered_id: UUID = Path(
        ...,
        description="검토 상태를 변경할 미응답 질문 ID입니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 미응답 질문 존재 확인
    query = (db_models.UnansweredQuestion.select()
                .where(db_models.UnansweredQuestion.id == unanswered_id))
    unanswered: Optional[db_items.UnansweredQuestion] = await db_manager.select_item(query)
    if not unanswered:
        return NotFoundResponse(
            message="등록되지 않은 미응답 질문입니다.",
            target=f"unanswered_id={unanswered_id}"
        )

    # 검토 상태 변경
    update_query = (db_models.UnansweredQuestion
                        .update(
                            review_status=payload.review_status.value,
                            reviewed_by=user_info.username,
                            reviewed_at=util.get_now(),
                        )
                        .where(db_models.UnansweredQuestion.id == unanswered_id))
    await db_manager.execute_query(update_query)

    logger.info(
        f"미응답 질문의 검토 상태가 변경되었습니다. "
        f"(unanswered_id={unanswered_id}, review_status={payload.review_status.value}, reviewed_by={user_info.username})"
    )

    return BaseMessageResponse(message="검토 상태가 변경되었습니다.")

@router.post("/feedback", summary="만족도 피드백 등록",
    description=(
        "챗봇 응답에 대한 만족도(1~5)와 코멘트를 등록합니다. (KAI-REQ-033)  \n"
        "- 일반 사용자용 API이며, 본인 세션의 챗봇(assistant) 메시지만 평가할 수 있습니다.  \n"
        "- 메시지당 피드백은 1건이며, 같은 메시지에 다시 등록하면 평점·내용·등록 일시가 갱신됩니다."
    ),
    responses={
        200: {"description": "피드백이 등록되었습니다.", "model": BaseMessageIdResponse[str]},
        400: {
            "description": "잘못된 요청입니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "invalid_role": {
                            "summary": "챗봇 응답 메시지가 아닙니다.",
                            "value": {
                                "message": "챗봇 응답 메시지에만 피드백을 등록할 수 있습니다.",
                                "target": "message_id={message_id}"
                            }
                        }
                    }
                }
            }
        },
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found_message": {
                            "summary": "등록되지 않은 메시지",
                            "value": {
                                "message": "등록되지 않은 메시지입니다.",
                                "target": "message_id={message_id}"
                            }
                        },
                        "not_found_session": {
                            "summary": "등록되지 않은 세션",
                            "value": {
                                "message": "등록되지 않은 세션입니다.",
                                "target": "session_id={session_id}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def add_user_feedback(
    payload: AddUserFeedbackPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 메시지 존재 확인
    message_query = (db_models.ChatMessage.select()
                        .where(db_models.ChatMessage.id == payload.message_id))
    message: Optional[db_items.ChatMessage] = await db_manager.select_item(message_query)
    if not message:
        return NotFoundResponse(
            message="등록되지 않은 메시지입니다.",
            target=f"message_id={payload.message_id}"
        )

    if message.role != ChatRole.ASSISTANT:
        return BadRequestResponse(
            message="챗봇 응답 메시지에만 피드백을 등록할 수 있습니다.",
            target=f"message_id={payload.message_id}"
        )

    # 세션 존재 및 소유자 확인
    session_query = (db_models.ChatSession.select()
                        .where(db_models.ChatSession.id == message.session_id))
    session: Optional[db_items.ChatSession] = await db_manager.select_item(session_query)
    if not session:
        return NotFoundResponse(
            message="등록되지 않은 세션입니다.",
            target=f"session_id={message.session_id}"
        )

    if session.user_name != user_info.username:
        return ForbiddenResponse(message="본인 세션의 메시지에만 피드백을 등록할 수 있습니다.")

    # 기존 피드백 확인 (메시지당 1건)
    existing_query = (db_models.UserFeedback.select()
                        .where(db_models.UserFeedback.message_id == payload.message_id))
    existing: Optional[db_items.UserFeedback] = await db_manager.select_item(existing_query)

    if existing:
        # 기존 피드백 갱신
        update_query = (db_models.UserFeedback
                            .update(
                                rating=payload.rating,
                                feedback_text=payload.feedback_text,
                                created_at=util.get_now(),
                            )
                            .where(db_models.UserFeedback.id == existing.id))
        await db_manager.execute_query(update_query)

        logger.info(f"피드백이 갱신되었습니다. (feedback_id={existing.id}, message_id={payload.message_id})")

        return BaseMessageIdResponse[str](
            message="피드백이 등록되었습니다.",
            id=str(existing.id)
        )

    # 신규 피드백 등록
    feedback_id = uuid.uuid4()
    insert_query = db_models.UserFeedback.insert(
        id=feedback_id,
        session_id=message.session_id,
        message_id=payload.message_id,
        rating=payload.rating,
        feedback_text=payload.feedback_text,
    )
    await db_manager.execute_query(insert_query)

    logger.info(f"피드백이 등록되었습니다. (feedback_id={feedback_id}, message_id={payload.message_id})")

    return BaseMessageIdResponse[str](
        message="피드백이 등록되었습니다.",
        id=str(feedback_id)
    )

@router.get("/feedback/list", summary="만족도 피드백 목록 조회",
    description=(
        "등록된 만족도 피드백 목록을 페이징 처리하여 조회합니다. (KAI-REQ-033)  \n"
        "사용자명·세션·평점·기간으로 필터링할 수 있으며 최신순으로 정렬됩니다."
    ),
    responses={
        200: {"description": "피드백 목록을 반환합니다.", "model": BaseListResponse[UserFeedbackListItem]},
        400: INVALID_PERIOD_RESPONSE,
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_feedback_list(
    page: int = Query(
        1, ge=1,
        description="조회할 페이지 번호입니다.",
        examples=[1, 2, 3, 4, 5]
    ),
    count: int = Query(
        10, ge=0, le=100,
        description="페이지당 항목 수입니다. (최대 100개, 0이면 전체 조회)",
        examples=[10, 20, 50, 100]
    ),
    user_name: Optional[str] = Query(
        None,
        description="사용자명으로 필터링합니다. (부분 일치)",
        examples=["20241234"]
    ),
    session_id: Optional[UUID] = Query(
        None,
        description="세션 ID로 필터링합니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    ),
    rating: Optional[int] = Query(
        None, ge=1, le=5,
        description="평점으로 필터링합니다. (1~5)",
        examples=[1, 5]
    ),
    start_date: Optional[date] = Query(
        None,
        description="조회 시작일입니다. (YYYY-MM-DD, 미지정 시 제한 없음)",
        examples=["2026-07-01"]
    ),
    end_date: Optional[date] = Query(
        None,
        description="조회 종료일입니다. (YYYY-MM-DD, 미지정 시 제한 없음)",
        examples=["2026-07-27"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    error_response = _validate_period(start_date, end_date)
    if error_response:
        return error_response

    # 필터 조건 추가
    conditions = []
    if user_name is not None:
        conditions.append(db_models.ChatSession.user_name.contains(user_name))
    if session_id is not None:
        conditions.append(db_models.UserFeedback.session_id == session_id)
    if rating is not None:
        conditions.append(db_models.UserFeedback.rating == rating)
    if start_date is not None:
        period_start, _ = _resolve_period(start_date, start_date)
        conditions.append(db_models.UserFeedback.created_at >= period_start)
    if end_date is not None:
        _, period_end = _resolve_period(end_date, end_date)
        conditions.append(db_models.UserFeedback.created_at <= period_end)

    join_condition = (db_models.UserFeedback.session_id == db_models.ChatSession.id)

    query = (db_models.UserFeedback
                .select(
                    db_models.UserFeedback.id,
                    db_models.UserFeedback.session_id,
                    db_models.UserFeedback.message_id,
                    db_models.UserFeedback.rating,
                    db_models.UserFeedback.feedback_text,
                    db_models.UserFeedback.created_at,
                    db_models.ChatSession.user_name.alias("user_name"),
                )
                .join(db_models.ChatSession, JOIN.LEFT_OUTER, on=join_condition))
    count_query = (db_models.UserFeedback
                    .select(fn.COUNT(db_models.UserFeedback.id).alias("count"))
                    .join(db_models.ChatSession, JOIN.LEFT_OUTER, on=join_condition))

    combined_condition = _combine_conditions(conditions)
    if combined_condition is not None:
        query = query.where(combined_condition)
        count_query = count_query.where(combined_condition)

    query = query.order_by(db_models.UserFeedback.created_at.desc())

    # 페이징 처리 (count가 0이면 전체 조회)
    if count > 0:
        offset = (page - 1) * count
        query = query.offset(offset).limit(count)

    # 총 개수 조회
    count_result = await db_manager.execute_query(count_query)
    total_count = count_result[0].count if count_result else 0

    # 목록 조회
    feedback_result = await db_manager.execute_query(query.dicts())
    items = [UserFeedbackListItem(**row) for row in feedback_result]

    return BaseListResponse[UserFeedbackListItem](
        total_pages=_calculate_total_pages(total_count, count),
        total_count=total_count,
        items=items
    )

@router.get("/logs/retrieval", summary="질의응답 검색 로그 조회",
    description=(
        "질의응답 과정의 검색 로그를 페이징 처리하여 조회합니다. (KAI-REQ-043)  \n"
        "세션·의도·기간으로 필터링할 수 있으며 최신순으로 정렬됩니다."
    ),
    responses={
        200: {"description": "검색 로그 목록을 반환합니다.", "model": BaseListResponse[db_items.RetrievalLog]},
        400: INVALID_PERIOD_RESPONSE,
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_retrieval_logs(
    page: int = Query(
        1, ge=1,
        description="조회할 페이지 번호입니다.",
        examples=[1, 2, 3, 4, 5]
    ),
    count: int = Query(
        10, ge=0, le=100,
        description="페이지당 항목 수입니다. (최대 100개, 0이면 전체 조회)",
        examples=[10, 20, 50, 100]
    ),
    session_id: Optional[UUID] = Query(
        None,
        description="세션 ID로 필터링합니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    ),
    intent: Optional[ChatIntent] = Query(
        None,
        description="감지 의도로 필터링합니다.",
        examples=list(ChatIntent)
    ),
    start_date: Optional[date] = Query(
        None,
        description="조회 시작일입니다. (YYYY-MM-DD, 미지정 시 제한 없음)",
        examples=["2026-07-01"]
    ),
    end_date: Optional[date] = Query(
        None,
        description="조회 종료일입니다. (YYYY-MM-DD, 미지정 시 제한 없음)",
        examples=["2026-07-27"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    error_response = _validate_period(start_date, end_date)
    if error_response:
        return error_response

    # 필터 조건 추가
    conditions = []
    if session_id is not None:
        conditions.append(db_models.RetrievalLog.session_id == session_id)
    if intent is not None:
        conditions.append(db_models.RetrievalLog.detected_intent == intent.value)
    if start_date is not None:
        period_start, _ = _resolve_period(start_date, start_date)
        conditions.append(db_models.RetrievalLog.created_at >= period_start)
    if end_date is not None:
        _, period_end = _resolve_period(end_date, end_date)
        conditions.append(db_models.RetrievalLog.created_at <= period_end)

    query = db_models.RetrievalLog.select()
    count_query = db_models.RetrievalLog.select(
        fn.COUNT(db_models.RetrievalLog.id).alias("count")
    )

    combined_condition = _combine_conditions(conditions)
    if combined_condition is not None:
        query = query.where(combined_condition)
        count_query = count_query.where(combined_condition)

    query = query.order_by(db_models.RetrievalLog.created_at.desc())

    # 페이징 처리 (count가 0이면 전체 조회)
    if count > 0:
        offset = (page - 1) * count
        query = query.offset(offset).limit(count)

    # 총 개수 조회
    count_result = await db_manager.execute_query(count_query)
    total_count = count_result[0].count if count_result else 0

    # 목록 조회
    items: list[db_items.RetrievalLog] = await db_manager.select_items(query)

    return BaseListResponse[db_items.RetrievalLog](
        total_pages=_calculate_total_pages(total_count, count),
        total_count=total_count,
        items=items
    )

@router.get("/logs/conversation", summary="대화 이력 로그 조회",
    description=(
        "대화 이력을 메시지 단위로 페이징 처리하여 조회합니다. (KAI-REQ-044)  \n"
        "사용자명·세션·역할·기간으로 필터링할 수 있으며 최신순으로 정렬됩니다."
    ),
    responses={
        200: {"description": "대화 이력 로그 목록을 반환합니다.", "model": BaseListResponse[ConversationLogItem]},
        400: INVALID_PERIOD_RESPONSE,
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_conversation_logs(
    page: int = Query(
        1, ge=1,
        description="조회할 페이지 번호입니다.",
        examples=[1, 2, 3, 4, 5]
    ),
    count: int = Query(
        10, ge=0, le=100,
        description="페이지당 항목 수입니다. (최대 100개, 0이면 전체 조회)",
        examples=[10, 20, 50, 100]
    ),
    user_name: Optional[str] = Query(
        None,
        description="사용자명으로 필터링합니다. (부분 일치)",
        examples=["20241234"]
    ),
    session_id: Optional[UUID] = Query(
        None,
        description="세션 ID로 필터링합니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    ),
    role: Optional[ChatRole] = Query(
        None,
        description="메시지 역할로 필터링합니다.",
        examples=list(ChatRole)
    ),
    start_date: Optional[date] = Query(
        None,
        description="조회 시작일입니다. (YYYY-MM-DD, 미지정 시 제한 없음)",
        examples=["2026-07-01"]
    ),
    end_date: Optional[date] = Query(
        None,
        description="조회 종료일입니다. (YYYY-MM-DD, 미지정 시 제한 없음)",
        examples=["2026-07-27"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    error_response = _validate_period(start_date, end_date)
    if error_response:
        return error_response

    # 필터 조건 추가
    conditions = []
    if user_name is not None:
        conditions.append(db_models.ChatSession.user_name.contains(user_name))
    if session_id is not None:
        conditions.append(db_models.ChatMessage.session_id == session_id)
    if role is not None:
        conditions.append(db_models.ChatMessage.role == role.value)
    if start_date is not None:
        period_start, _ = _resolve_period(start_date, start_date)
        conditions.append(db_models.ChatMessage.created_at >= period_start)
    if end_date is not None:
        _, period_end = _resolve_period(end_date, end_date)
        conditions.append(db_models.ChatMessage.created_at <= period_end)

    join_condition = (db_models.ChatMessage.session_id == db_models.ChatSession.id)

    query = (db_models.ChatMessage
                .select(
                    db_models.ChatMessage.id,
                    db_models.ChatMessage.session_id,
                    db_models.ChatMessage.role,
                    db_models.ChatMessage.content,
                    db_models.ChatMessage.detected_intent,
                    db_models.ChatMessage.model_name,
                    db_models.ChatMessage.latency_ms,
                    db_models.ChatMessage.is_answered,
                    db_models.ChatMessage.created_at,
                    db_models.ChatSession.user_name.alias("user_name"),
                )
                .join(db_models.ChatSession, JOIN.LEFT_OUTER, on=join_condition))
    count_query = (db_models.ChatMessage
                    .select(fn.COUNT(db_models.ChatMessage.id).alias("count"))
                    .join(db_models.ChatSession, JOIN.LEFT_OUTER, on=join_condition))

    combined_condition = _combine_conditions(conditions)
    if combined_condition is not None:
        query = query.where(combined_condition)
        count_query = count_query.where(combined_condition)

    query = query.order_by(db_models.ChatMessage.created_at.desc())

    # 페이징 처리 (count가 0이면 전체 조회)
    if count > 0:
        offset = (page - 1) * count
        query = query.offset(offset).limit(count)

    # 총 개수 조회
    count_result = await db_manager.execute_query(count_query)
    total_count = count_result[0].count if count_result else 0

    # 목록 조회
    log_result = await db_manager.execute_query(query.dicts())
    items = [ConversationLogItem(**row) for row in log_result]

    return BaseListResponse[ConversationLogItem](
        total_pages=_calculate_total_pages(total_count, count),
        total_count=total_count,
        items=items
    )

@router.get("/logs/user", summary="사용자별 이용 로그 조회",
    description=(
        "사용자별 챗봇 이용 현황을 페이징 처리하여 조회합니다. (KAI-REQ-045)  \n"
        "- 세션 수: 기간 내 생성된 세션 수  \n"
        "- 질문 수: 기간 내 사용자(user) 메시지 수  \n"
        "- 최근 활동일: 기간 내 세션의 마지막 활동 일시 중 가장 최근 값  \n"
        "- 평균 만족도: 기간 내 등록한 피드백 평점의 평균 (평가가 없으면 null)  \n"
        "- 최근 활동일 기준 내림차순으로 정렬됩니다.  \n"
        "- 기본 조회 기간: 최근 30일"
    ),
    responses={
        200: {"description": "사용자별 이용 로그 목록을 반환합니다.", "model": BaseListResponse[UserUsageLogItem]},
        400: INVALID_PERIOD_RESPONSE,
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_user_usage_logs(
    page: int = Query(
        1, ge=1,
        description="조회할 페이지 번호입니다.",
        examples=[1, 2, 3, 4, 5]
    ),
    count: int = Query(
        10, ge=0, le=100,
        description="페이지당 항목 수입니다. (최대 100개, 0이면 전체 조회)",
        examples=[10, 20, 50, 100]
    ),
    user_name: Optional[str] = Query(
        None,
        description="사용자명으로 필터링합니다. (부분 일치)",
        examples=["20241234"]
    ),
    start_date: Optional[date] = Query(
        None,
        description="조회 시작일입니다. (YYYY-MM-DD, 기본값: 종료일 기준 29일 전)",
        examples=["2026-07-01"]
    ),
    end_date: Optional[date] = Query(
        None,
        description="조회 종료일입니다. (YYYY-MM-DD, 기본값: 오늘)",
        examples=["2026-07-27"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    error_response = _validate_period(start_date, end_date)
    if error_response:
        return error_response

    period_start, period_end = _resolve_period(start_date, end_date)

    conditions = [
        db_models.ChatSession.created_at >= period_start,
        db_models.ChatSession.created_at <= period_end,
    ]
    if user_name is not None:
        conditions.append(db_models.ChatSession.user_name.contains(user_name))

    session_condition = _combine_conditions(conditions)

    # 총 사용자 수 조회
    count_query = (db_models.ChatSession
                    .select(fn.COUNT(fn.DISTINCT(db_models.ChatSession.user_name)).alias("count"))
                    .where(session_condition))
    count_result = await db_manager.execute_query(count_query)
    total_count = count_result[0].count if count_result else 0

    # 사용자별 세션 수 / 최근 활동일 집계
    session_query = (db_models.ChatSession
                        .select(
                            db_models.ChatSession.user_name,
                            fn.COUNT(db_models.ChatSession.id).alias("session_count"),
                            fn.MAX(db_models.ChatSession.last_active_at).alias("last_active_at"),
                        )
                        .where(session_condition)
                        .group_by(db_models.ChatSession.user_name)
                        .order_by(fn.MAX(db_models.ChatSession.last_active_at).desc()))

    # 페이징 처리 (count가 0이면 전체 조회)
    if count > 0:
        offset = (page - 1) * count
        session_query = session_query.offset(offset).limit(count)

    session_result = await db_manager.execute_query(session_query.dicts())
    session_rows = list(session_result)
    page_user_names = [row["user_name"] for row in session_rows]

    # 사용자별 질문 수 집계
    question_counts: dict[str, int] = {}
    feedback_stats: dict[str, dict] = {}
    if page_user_names:
        question_query = (db_models.ChatMessage
                            .select(
                                db_models.ChatSession.user_name.alias("user_name"),
                                fn.COUNT(db_models.ChatMessage.id).alias("question_count"),
                            )
                            .join(
                                db_models.ChatSession,
                                on=(db_models.ChatMessage.session_id == db_models.ChatSession.id)
                            )
                            .where(
                                (db_models.ChatMessage.role == ChatRole.USER.value) &
                                (db_models.ChatMessage.created_at >= period_start) &
                                (db_models.ChatMessage.created_at <= period_end) &
                                (db_models.ChatSession.user_name.in_(page_user_names))
                            )
                            .group_by(db_models.ChatSession.user_name)
                            .dicts())
        question_result = await db_manager.execute_query(question_query)
        question_counts = {row["user_name"]: row["question_count"] for row in question_result}

        # 사용자별 만족도 집계
        feedback_query = (db_models.UserFeedback
                            .select(
                                db_models.ChatSession.user_name.alias("user_name"),
                                fn.COUNT(db_models.UserFeedback.id).alias("feedback_count"),
                                fn.AVG(db_models.UserFeedback.rating).alias("average_rating"),
                            )
                            .join(
                                db_models.ChatSession,
                                on=(db_models.UserFeedback.session_id == db_models.ChatSession.id)
                            )
                            .where(
                                (db_models.UserFeedback.created_at >= period_start) &
                                (db_models.UserFeedback.created_at <= period_end) &
                                (db_models.ChatSession.user_name.in_(page_user_names))
                            )
                            .group_by(db_models.ChatSession.user_name)
                            .dicts())
        feedback_result = await db_manager.execute_query(feedback_query)
        feedback_stats = {row["user_name"]: row for row in feedback_result}

    items = []
    for row in session_rows:
        target_user_name = row["user_name"]
        feedback_stat = feedback_stats.get(target_user_name)
        average_rating = _to_float(feedback_stat["average_rating"]) if feedback_stat else None

        items.append(UserUsageLogItem(
            user_name=target_user_name,
            session_count=row["session_count"],
            question_count=question_counts.get(target_user_name, 0),
            feedback_count=feedback_stat["feedback_count"] if feedback_stat else 0,
            average_rating=round(average_rating, 2) if average_rating is not None else None,
            last_active_at=row["last_active_at"],
        ))

    return BaseListResponse[UserUsageLogItem](
        total_pages=_calculate_total_pages(total_count, count),
        total_count=total_count,
        items=items
    )
