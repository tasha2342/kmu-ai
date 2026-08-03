import orjson
import asyncio

from datetime import timedelta

from logging import Logger

from typing import Any, AsyncGenerator, Optional

from uuid import UUID, uuid4

from peewee import fn

from fastapi import APIRouter, Depends, Path, Query

from sse_starlette.sse import EventSourceResponse

from app.config import config, env
from app.exceptions.api_exception import DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN
from app.payloads.v1.chat import (
    CreateChatSessionPayload,
    SendChatMessagePayload,
    UpdateChatSessionPayload,
)
from app.responses.base import BaseListResponse, BaseMessageResponse
from app.responses.v1.chat import (
    ChatMessageItem,
    ChatMessageResponse,
    ChatSessionDetailResponse,
    ChatSessionItem,
    ChatSourceItem,
    ChatStreamEvent,
)
from app.responses.exception import (
    BadRequestResponse,
    ForbiddenResponse,
    NotFoundResponse,
)
from app.models.auth import TokenUserInfo
from app.models.api.exception import (
    BadRequestError,
    NotFoundError,
)
from app.models.api.common import OrderBy
from app.models.enum import (
    ChatIntent,
    ChatLanguage,
    ChatRole,
    ChatSessionStatus,
    Language,
    UnansweredReason,
)
from app.utils.chat_graph import (
    get_retrieval_collection_name,
    localized_message,
    run_chat_graph,
    update_session_summary,
)
from app.utils.database import DatabaseManager, get_db_manager
from app.utils.litellm import stream_chat_completion
from app.utils.logger import get_api_logger
from app.utils.pii_mask import apply_masking, load_active_rules
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.auth as auth
import app.utils.common as util


router = APIRouter()


TITLE_MAX_LENGTH = 30
"""첫 질문으로 세션 제목을 자동 생성할 때 사용할 최대 길이"""


def _is_admin(user_info: TokenUserInfo) -> bool:
    """관리자 권한 보유 여부를 확인합니다.

    Args:
        user_info (TokenUserInfo): 사용자 정보

    Returns:
        bool: 관리자 여부
    """

    return bool(set(user_info.roles or []) & set(config.auth.admin_roles or []))

def _can_access_session(user_info: TokenUserInfo, session: db_items.ChatSession) -> bool:
    """세션 접근 권한을 확인합니다. (KAI-REQ-035 개인정보 보호)

    본인 세션이거나 관리자만 접근할 수 있습니다.

    Args:
        user_info (TokenUserInfo): 사용자 정보
        session (db_items.ChatSession): 세션 정보

    Returns:
        bool: 접근 가능 여부
    """

    return session.user_name == user_info.username or _is_admin(user_info)

def _parse_uuid(value: str) -> Optional[UUID]:
    """문자열을 UUID로 변환합니다.

    Args:
        value (str): 변환할 문자열

    Returns:
        Optional[UUID]: 변환된 UUID 또는 None
    """

    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None

def _dumps(data: dict) -> str:
    """SSE 이벤트 데이터를 JSON 문자열로 직렬화합니다.

    Args:
        data (dict): 직렬화할 데이터

    Returns:
        str: JSON 문자열
    """

    return orjson.dumps(data).decode("utf-8")

def _build_title(message: str) -> str:
    """첫 질문으로 세션 제목을 생성합니다.

    Args:
        message (str): 사용자 질문

    Returns:
        str: 세션 제목
    """

    title = " ".join((message or "").split())
    if len(title) > TITLE_MAX_LENGTH:
        title = f"{title[:TITLE_MAX_LENGTH]}..."
    return title or "새 대화"

def _is_idle_expired(session: db_items.ChatSession) -> bool:
    """미입력 자동 종료 대상인지 확인합니다. (KAI-REQ-039)

    환경 변수 `ENABLE_SESSION_IDLE_TIMEOUT`이 false면 항상 False를 반환합니다.
    기준 시간은 `SESSION_IDLE_TIMEOUT_MINUTES`(분)입니다.

    Args:
        session (db_items.ChatSession): 세션 정보

    Returns:
        bool: 자동 종료 대상 여부
    """

    if not env.ENABLE_SESSION_IDLE_TIMEOUT:
        return False

    timeout = env.SESSION_IDLE_TIMEOUT_MINUTES
    if not timeout or timeout <= 0:
        return False
    return session.last_active_at + timedelta(minutes=timeout) < util.get_now()


async def _get_session(db_manager: DatabaseManager, session_id: UUID) -> Optional[db_items.ChatSession]:
    """세션을 조회합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        session_id (UUID): 세션 ID

    Returns:
        Optional[db_items.ChatSession]: 세션 정보 또는 None
    """

    query = (db_models.ChatSession.select()
             .where(db_models.ChatSession.id == session_id))
    return await db_manager.select_item(query)

async def _create_session(
    db_manager: DatabaseManager,
    user_name: str,
    language: ChatLanguage,
    title: Optional[str] = None,
    profile: Optional[dict] = None,
) -> db_items.ChatSession:
    """세션을 생성하고 생성된 세션을 반환합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        user_name (str): 사용자명
        language (ChatLanguage): 대화 언어 (`auto`면 질문마다 감지)
        title (Optional[str]): 세션 제목 (Default: None)
        profile (Optional[dict]): 개인화 컨텍스트 (Default: None)

    Returns:
        db_items.ChatSession: 생성된 세션 정보
    """

    session_id = uuid4()
    query = db_models.ChatSession.insert(
        id=session_id,
        user_name=user_name,
        title=title,
        language=language,
        status=ChatSessionStatus.ACTIVE,
        profile=profile,
    )
    await db_manager.execute_query(query)
    return await _get_session(db_manager, session_id)

def _resolve_language(
    payload_language: Optional[ChatLanguage],
    session_language: Optional[ChatLanguage],
) -> Optional[Language]:
    """이번 질문의 명시 지정 응답 언어를 결정합니다. (KAI-REQ-029)

    사용자가 UI에서 언어를 고른 경우가 자동 감지보다 우선해야 하므로, 우선순위를
    요청 → 세션 → 자동 순으로 둡니다. 요청이 `auto`를 명시하면 세션 설정까지 건너뛰고
    자동으로 갑니다. 화면에서 "자동"을 고른 것 역시 사용자의 명시적 선택이기 때문입니다.

    Args:
        payload_language (Optional[ChatLanguage]): 요청에 실린 언어 (없으면 세션 설정을 따름)
        session_language (Optional[ChatLanguage]): 세션에 저장된 언어

    Returns:
        Optional[Language]: 명시 지정된 응답 언어. None이면 발화에서 자동 감지합니다.
    """

    for candidate in (payload_language, session_language):
        if candidate is None:
            continue
        if isinstance(candidate, str) and not isinstance(candidate, ChatLanguage):
            try:
                candidate = ChatLanguage(candidate)
            except ValueError:
                continue
        return candidate.to_language()
    return None


async def _load_history(db_manager: DatabaseManager, session_id: UUID, limit: int) -> list[dict]:
    """프롬프트에 포함할 최근 대화 이력을 조회합니다. (KAI-REQ-016 문맥 유지)

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        session_id (UUID): 세션 ID
        limit (int): 조회할 메시지 수

    Returns:
        list[dict]: 오래된 순으로 정렬된 대화 이력
    """

    if limit <= 0:
        return []

    query = (db_models.ChatMessage.select()
             .where(db_models.ChatMessage.session_id == session_id)
             .where(db_models.ChatMessage.role != ChatRole.SYSTEM.value)
             .order_by(db_models.ChatMessage.created_at.desc())
             .limit(limit))
    messages: list[db_items.ChatMessage] = await db_manager.select_items(query)

    history = [
        {"role": message.role.value if isinstance(message.role, ChatRole) else str(message.role),
         "content": message.content}
        for message in reversed(messages)
    ]
    return history


async def _stream_answer(
    state: dict,
    outcome: dict,
    user_info: TokenUserInfo,
    db_manager: DatabaseManager,
    logger: Logger,
) -> AsyncGenerator[str, None]:
    """최종 응답 텍스트를 조각 단위로 생성합니다.

    그래프가 정해진 문구를 채워 준 경우(abuse/fallback/ambiguous/personal)에는 LLM을 호출하지 않고
    문구를 그대로 반환합니다. 모델을 사용할 수 없으면 500으로 실패시키지 않고 안내 문구로 대체합니다.

    Args:
        state (dict): 그래프 실행 결과 상태
        outcome (dict): 생성 결과를 기록할 딕셔너리 (모델명·미응답 사유)
        user_info (TokenUserInfo): 사용자 정보
        db_manager (DatabaseManager): 데이터베이스 매니저
        logger (Logger): 로거

    Yields:
        str: 응답 텍스트 조각
    """

    # 정형 문구도 응답 언어를 따라야 합니다. 설정에는 한국어 한 벌만 있으므로
    # chat_graph.localized_message가 언어별 문구를 골라 줍니다. (KAI-REQ-029)
    language = state.get("language")

    if not state.get("needs_generation"):
        yield state.get("answer") or localized_message("fallback", language)
        return

    outcome["model_name"] = config.chatbot.text_model
    emitted = False

    try:
        async for delta in stream_chat_completion(
            model_name=config.chatbot.text_model,
            messages=state.get("messages") or [],
            user_info=user_info,
            db_manager=db_manager,
            usage_source="chatbot",
        ):
            emitted = True
            yield delta
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("챗봇 응답 생성 중 오류가 발생했습니다.")
        outcome["unanswered_reason"] = UnansweredReason.MODEL_ERROR
        outcome["failed"] = True
        fallback = localized_message("fallback", language)
        yield f"\n\n{fallback}" if emitted else fallback

async def _persist_turn(
    db_manager: DatabaseManager,
    user_info: TokenUserInfo,
    session: db_items.ChatSession,
    history: list[dict],
    state: dict,
    query_text: str,
    answer: str,
    user_message_id: UUID,
    assistant_message_id: UUID,
    outcome: dict,
    latency_ms: int,
    logger: Logger,
):
    """한 번의 대화 턴 결과를 저장합니다.

    응답 메시지, 검색 로그(KAI-REQ-043), 미응답 질문(KAI-REQ-031/040), 세션 메타 정보를 갱신합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        user_info (TokenUserInfo): 사용자 정보
        session (db_items.ChatSession): 세션 정보
        history (list[dict]): 이번 턴 이전의 대화 이력
        state (dict): 그래프 실행 결과 상태
        query_text (str): 사용자 질문
        answer (str): 챗봇 응답
        user_message_id (UUID): 사용자 메시지 ID
        assistant_message_id (UUID): 챗봇 메시지 ID
        outcome (dict): 생성 결과 (모델명·미응답 사유)
        latency_ms (int): 응답 생성 시간(ms)
        logger (Logger): 로거
    """

    intent: ChatIntent = state.get("intent") or ChatIntent.UNKNOWN
    sources: list[dict] = state.get("sources") or []
    unanswered_reason: Optional[UnansweredReason] = (
        outcome.get("unanswered_reason") or state.get("unanswered_reason")
    )
    is_answered = unanswered_reason is None

    # 저장 전 개인정보 마스킹 (활성 규칙)
    masking_rules = await load_active_rules(db_manager)
    answer = apply_masking(answer, masking_rules)
    query_text = apply_masking(query_text, masking_rules)
    search_query = state.get("search_query")
    if search_query:
        search_query = apply_masking(search_query, masking_rules)

    # 챗봇 응답 메시지 저장
    query = db_models.ChatMessage.insert(
        id=assistant_message_id,
        session_id=session.id,
        role=ChatRole.ASSISTANT,
        content=answer,
        detected_intent=intent,
        sources=sources or None,
        model_name=outcome.get("model_name"),
        latency_ms=latency_ms,
        is_answered=is_answered,
    )
    await db_manager.execute_query(query)

    # 검색 로그 저장 (KAI-REQ-043)
    if state.get("retrieval_attempted"):
        selected_source_id = None
        if sources:
            selected_source_id = _parse_uuid(sources[0].get("source_id"))
        # 실제로 검색에 쓴 질의를 남깁니다. 후속 질문은 대화 맥락을 반영해 재작성되므로
        # 원문("그럼 기간은?")을 남기면 왜 그 근거가 나왔는지 로그로 추적할 수 없습니다.
        query = db_models.RetrievalLog.insert(
            id=uuid4(),
            session_id=session.id,
            message_id=user_message_id,
            query_text=search_query or query_text,
            detected_intent=intent,
            collection_name=get_retrieval_collection_name(),
            selected_source_id=selected_source_id,
            result_count=len(sources),
            latency_ms=state.get("retrieval_latency_ms") or 0,
        )
        await db_manager.execute_query(query)

    # 미응답 질문 저장 (KAI-REQ-031/040)
    if unanswered_reason:
        query = db_models.UnansweredQuestion.insert(
            id=uuid4(),
            session_id=session.id,
            message_id=user_message_id,
            question_text=query_text,
            reason=unanswered_reason,
        )
        await db_manager.execute_query(query)

    # 세션 요약 갱신 (KAI-REQ-041)
    # 정해진 문구 경로는 그래프의 summarize 노드가 이미 갱신했으므로 생성 경로만 여기서 처리합니다.
    new_summary = state.get("summary_updated")
    if new_summary is None and state.get("needs_generation") and answer:
        new_summary = await update_session_summary(
            db_manager=db_manager,
            user_info=user_info,
            previous_summary=session.summary,
            history=history,
            query=query_text,
            answer=answer,
            message_count=(session.message_count or 0) + 2,
            logger=logger,
        )

    # 세션 메타 정보 갱신
    now = util.get_now()
    update_data: dict[str, Any] = {
        "message_count": db_models.ChatSession.message_count + 2,
        "last_active_at": now,
        "updated_at": now,
    }
    if not session.title:
        update_data["title"] = _build_title(query_text)
    if new_summary:
        update_data["summary"] = new_summary

    query = (db_models.ChatSession.update(**update_data)
             .where(db_models.ChatSession.id == session.id))
    await db_manager.execute_query(query)


@router.post("/session/create", summary="대화 세션 생성",
    description=(
        "새로운 대화 세션을 생성합니다.  \n"
        "- 세션은 요청한 사용자 계정에 귀속되며, 본인과 관리자만 조회할 수 있습니다. (KAI-REQ-035)  \n"
        "- 제목을 생략하면 첫 질문을 기준으로 자동 생성됩니다."
    ),
    responses={
        200: {"description": "생성된 세션 정보를 반환합니다.", "model": ChatSessionItem},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def create_chat_session(
    payload: CreateChatSessionPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    session = await _create_session(
        db_manager=db_manager,
        user_name=user_info.username,
        language=payload.language,
        title=payload.title,
        profile=payload.profile,
    )

    logger.info(f"대화 세션이 생성되었습니다. (session_id={session.id})")

    return ChatSessionItem.from_session(session)

@router.get("/session/list", summary="대화 세션 목록 조회",
    description=(
        "내 대화 세션 목록을 페이징 처리하여 조회합니다.  \n"
        "- 관리자는 `user_name`으로 다른 사용자의 세션을 조회할 수 있습니다.  \n"
        "- `status`로 진행 중/종료된 세션을 구분해 조회할 수 있습니다."
    ),
    responses={
        200: {"description": "대화 세션 목록을 반환합니다.", "model": BaseListResponse[ChatSessionItem]},
        403: {
            "description": "접근 권한이 없습니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "forbidden_user_name": {
                            "summary": "다른 사용자의 세션 조회",
                            "value": {"message": "다른 사용자의 대화 세션을 조회할 권한이 없습니다."}
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_chat_session_list(
    page: int = Query(
        1, ge=1,
        description="조회할 페이지 번호입니다.",
        examples=[1, 2, 3]
    ),
    count: int = Query(
        10, ge=0, le=100,
        description="페이지당 항목 수입니다. (최대 100개, 0이면 전체 조회)",
        examples=[10, 20, 50]
    ),
    status: Optional[ChatSessionStatus] = Query(
        None,
        description="세션 상태로 필터링합니다.",
        examples=list(ChatSessionStatus)
    ),
    search: Optional[str] = Query(
        None,
        description="세션 제목으로 검색합니다.",
        examples=["수강신청"]
    ),
    user_name: Optional[str] = Query(
        None,
        description=(
            "조회할 사용자명입니다. (관리자 전용)  \n"
            "생략하면 본인의 세션만 조회합니다."
        ),
        examples=["20241234"]
    ),
    order_by: Optional[OrderBy] = Query(
        OrderBy.CREATED_AT_DESC,
        description="세션 목록 정렬 방식입니다.",
        examples=list(OrderBy)
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    # 조회 대상 사용자 확인 (본인 또는 관리자)
    target_user_name = user_name or user_info.username
    if target_user_name != user_info.username and not _is_admin(user_info):
        return ForbiddenResponse(message="다른 사용자의 대화 세션을 조회할 권한이 없습니다.")

    query = db_models.ChatSession.select()
    count_query = db_models.ChatSession.select(fn.COUNT(db_models.ChatSession.id).alias("count"))

    conditions = [db_models.ChatSession.user_name == target_user_name]
    if status is not None:
        conditions.append(db_models.ChatSession.status == status.value)
    if search is not None:
        conditions.append(db_models.ChatSession.title.contains(search))

    combined_condition = conditions[0]
    for condition in conditions[1:]:
        combined_condition = combined_condition & condition
    query = query.where(combined_condition)
    count_query = count_query.where(combined_condition)

    # 정렬 적용
    if order_by == OrderBy.CREATED_AT_ASC:
        query = query.order_by(db_models.ChatSession.created_at.asc())
    elif order_by == OrderBy.UPDATED_AT_ASC:
        query = query.order_by(db_models.ChatSession.updated_at.asc())
    elif order_by == OrderBy.UPDATED_AT_DESC:
        query = query.order_by(db_models.ChatSession.updated_at.desc())
    else:
        query = query.order_by(db_models.ChatSession.created_at.desc())

    # 페이징 처리 (count가 0이면 전체 조회)
    if count > 0:
        query = query.offset((page - 1) * count).limit(count)

    count_result = await db_manager.execute_query(count_query)
    total_count = count_result[0].count if count_result else 0

    sessions: list[db_items.ChatSession] = await db_manager.select_items(query)

    if count > 0:
        total_pages = (total_count + count - 1) // count if total_count > 0 else 1
    else:
        total_pages = 1

    return BaseListResponse[ChatSessionItem](
        total_pages=total_pages,
        total_count=total_count,
        items=[ChatSessionItem.from_session(session) for session in sessions]
    )

@router.get("/session/{session_id}", summary="대화 세션 상세 조회",
    description=(
        "대화 세션 정보와 메시지 이력을 조회합니다. (KAI-REQ-045)  \n"
        "- 본인 세션이거나 관리자만 조회할 수 있습니다. (KAI-REQ-035)  \n"
        "- 메시지는 오래된 순으로 반환합니다."
    ),
    responses={
        200: {"description": "세션 정보와 메시지 이력을 반환합니다.", "model": ChatSessionDetailResponse},
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 세션",
                            "value": {
                                "message": "대화 세션을 찾을 수 없습니다.",
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
async def get_chat_session(
    session_id: str = Path(
        ...,
        description="조회할 세션 ID입니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    ),
    message_limit: int = Query(
        0, ge=0, le=500,
        description="조회할 최근 메시지 수입니다. (0이면 전체 조회)",
        examples=[0, 20, 50]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    parsed_id = _parse_uuid(session_id)
    if not parsed_id:
        return BadRequestResponse(
            message="세션 ID 형식이 올바르지 않습니다.",
            target=f"session_id={session_id}"
        )

    session = await _get_session(db_manager, parsed_id)
    if not session:
        return NotFoundResponse(
            message="대화 세션을 찾을 수 없습니다.",
            target=f"session_id={session_id}"
        )

    if not _can_access_session(user_info, session):
        return ForbiddenResponse(message="해당 대화 세션에 접근할 권한이 없습니다.")

    # 메시지 이력 조회
    if message_limit > 0:
        query = (db_models.ChatMessage.select()
                 .where(db_models.ChatMessage.session_id == parsed_id)
                 .order_by(db_models.ChatMessage.created_at.desc())
                 .limit(message_limit))
        messages: list[db_items.ChatMessage] = await db_manager.select_items(query)
        messages = list(reversed(messages))
    else:
        query = (db_models.ChatMessage.select()
                 .where(db_models.ChatMessage.session_id == parsed_id)
                 .order_by(db_models.ChatMessage.created_at.asc()))
        messages = await db_manager.select_items(query)

    return ChatSessionDetailResponse(
        session=ChatSessionItem.from_session(session),
        messages=[ChatMessageItem.from_message(message) for message in messages]
    )

@router.patch("/session/{session_id}", summary="대화 세션 수정",
    description=(
        "대화 세션의 제목을 수정하거나 대화를 종료합니다.  \n"
        "- `status=closed`로 대화를 종료할 수 있습니다.  \n"
        "- `status=active`로 종료된 대화를 다시 이어갈 수 있습니다.  \n"
        "- `idle_closed`는 미입력 자동 종료 시 서버가 설정하므로 직접 지정할 수 없습니다. (KAI-REQ-039)"
    ),
    responses={
        200: {"description": "수정된 세션 정보를 반환합니다.", "model": ChatSessionItem},
        400: {
            "description": "잘못된 요청입니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "invalid_status": {
                            "summary": "직접 지정할 수 없는 상태",
                            "value": {
                                "message": "미입력 자동 종료 상태는 직접 지정할 수 없습니다.",
                                "target": "status=idle_closed"
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
                        "not_found": {
                            "summary": "등록되지 않은 세션",
                            "value": {
                                "message": "대화 세션을 찾을 수 없습니다.",
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
async def update_chat_session(
    payload: UpdateChatSessionPayload,
    session_id: str = Path(
        ...,
        description="수정할 세션 ID입니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    parsed_id = _parse_uuid(session_id)
    if not parsed_id:
        return BadRequestResponse(
            message="세션 ID 형식이 올바르지 않습니다.",
            target=f"session_id={session_id}"
        )

    session = await _get_session(db_manager, parsed_id)
    if not session:
        return NotFoundResponse(
            message="대화 세션을 찾을 수 없습니다.",
            target=f"session_id={session_id}"
        )

    if not _can_access_session(user_info, session):
        return ForbiddenResponse(message="해당 대화 세션에 접근할 권한이 없습니다.")

    update_data: dict[str, Any] = {}
    payload_set = payload.model_dump(exclude_unset=True)

    if payload_set.get("title") is not None:
        update_data["title"] = payload.title
    if payload_set.get("status") is not None:
        if payload.status == ChatSessionStatus.IDLE_CLOSED:
            return BadRequestResponse(
                message="미입력 자동 종료 상태는 직접 지정할 수 없습니다.",
                target=f"status={payload.status.value}"
            )
        update_data["status"] = payload.status
        if payload.status == ChatSessionStatus.ACTIVE:
            update_data["last_active_at"] = util.get_now()

    if update_data:
        update_data["updated_at"] = util.get_now()
        query = (db_models.ChatSession.update(**update_data)
                 .where(db_models.ChatSession.id == parsed_id))
        await db_manager.execute_query(query)
        session = await _get_session(db_manager, parsed_id)

        logger.debug(f"대화 세션이 수정되었습니다. (session_id={session_id})")

    return ChatSessionItem.from_session(session)

@router.delete("/session/{session_id}", summary="대화 세션 삭제",
    description=(
        "대화 세션과 관련 데이터를 삭제합니다. (KAI-REQ-035 개인정보 보호)  \n"
        "- 세션의 모든 메시지를 삭제합니다.  \n"
        "- 질문 원문이 남는 검색 로그도 함께 삭제합니다.  \n"
        "- 미응답 질문은 FAQ 보강을 위한 관리자 검토 대상이므로 유지됩니다. (KAI-REQ-031)"
    ),
    responses={
        200: {"description": "세션 삭제 결과를 반환합니다.", "model": BaseMessageResponse},
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 세션",
                            "value": {
                                "message": "대화 세션을 찾을 수 없습니다.",
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
async def delete_chat_session(
    session_id: str = Path(
        ...,
        description="삭제할 세션 ID입니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    parsed_id = _parse_uuid(session_id)
    if not parsed_id:
        return BadRequestResponse(
            message="세션 ID 형식이 올바르지 않습니다.",
            target=f"session_id={session_id}"
        )

    session = await _get_session(db_manager, parsed_id)
    if not session:
        return NotFoundResponse(
            message="대화 세션을 찾을 수 없습니다.",
            target=f"session_id={session_id}"
        )

    if not _can_access_session(user_info, session):
        return ForbiddenResponse(message="해당 대화 세션에 접근할 권한이 없습니다.")

    query = (db_models.ChatMessage.delete()
             .where(db_models.ChatMessage.session_id == parsed_id))
    await db_manager.execute_query(query)

    query = (db_models.RetrievalLog.delete()
             .where(db_models.RetrievalLog.session_id == parsed_id))
    await db_manager.execute_query(query)

    query = (db_models.ChatSession.delete()
             .where(db_models.ChatSession.id == parsed_id))
    await db_manager.execute_query(query)

    logger.info(f"대화 세션이 삭제되었습니다. (session_id={session_id})")

    return BaseMessageResponse(message="대화 세션이 성공적으로 삭제되었습니다.")

@router.post("/message", summary="대화 메시지 전송",
    description=(
        "질문을 전송하고 챗봇 응답을 받습니다.  \n"
        "- `session_id`를 생략하면 세션을 자동으로 생성합니다.  \n"
        "- LangGraph 오케스트레이션으로 의도를 분류하고(KAI-REQ-030), 학사·취업·문서 문의는 "
        "FAQ 지식베이스를 검색해 근거와 함께 답변합니다. (KAI-REQ-015)  \n"
        "- 비속어·모호한 질문·개인정보 조회·검색 실패는 정해진 안내 문구로 응답합니다. "
        "(KAI-REQ-037/038/040)  \n"
        "- 개인 학적·성적 등은 학내 연계 API가 제공되기 전까지 안내 문구와 바로가기만 제공하며, "
        "개인 데이터를 임의로 생성하지 않습니다. (KAI-REQ-035)  \n"
        "- 미입력으로 자동 종료 대상이 된 세션은 종료 처리 후 새 세션에서 답변합니다. (KAI-REQ-039)  \n"
        "- 벡터 검색이나 모델을 사용할 수 없는 경우에도 오류 대신 안내 문구로 응답합니다.  \n"
        "  \n"
        "**`stream=true` (기본값)**: `text/event-stream`으로 응답합니다.  \n"
        "- `session`: 세션/메시지 ID와 감지 의도  \n"
        "- `sources`: 응답 근거 목록  \n"
        "- `delta`: 응답 텍스트 조각  \n"
        "- `done`: 응답 완료 정보 (미응답 사유 포함)  \n"
        "- `error`: 오류 안내  \n"
        "  \n"
        "**`stream=false`**: 응답이 완성된 뒤 JSON으로 한 번에 반환합니다."
    ),
    responses={
        200: {
            "description": (
                "`stream=false`이면 대화 응답을 반환하고, "
                "`stream=true`이면 SSE 스트림(`text/event-stream`)을 반환합니다."
            ),
            "model": ChatMessageResponse,
            "content": {
                "text/event-stream": {
                    "schema": ChatStreamEvent.model_json_schema(),
                    "example": (
                        "event: session\n"
                        "data: {\"session_id\": \"8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f\"}\n\n"
                        "event: delta\n"
                        "data: {\"content\": \"2026학년도 1학기 수강신청 기간은 \"}\n\n"
                        "event: done\n"
                        "data: {\"is_answered\": true}\n\n"
                    )
                }
            }
        },
        400: {
            "description": "잘못된 요청입니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "closed_session": {
                            "summary": "종료된 세션",
                            "value": {
                                "message": "이미 종료된 대화 세션입니다. 새로운 대화를 시작해 주세요.",
                                "target": "session_id={session_id}"
                            }
                        },
                        "invalid_session_id": {
                            "summary": "잘못된 세션 ID 형식",
                            "value": {
                                "message": "세션 ID 형식이 올바르지 않습니다.",
                                "target": "session_id={session_id}"
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
                        "not_found": {
                            "summary": "등록되지 않은 세션",
                            "value": {
                                "message": "대화 세션을 찾을 수 없습니다.",
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
async def send_chat_message(
    payload: SendChatMessagePayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    started = util.get_now()
    notice: Optional[str] = None
    # 미입력 자동 종료가 일어났는지 여부 (KAI-REQ-039). 안내 문구는 언어 판정 후에 만듭니다.
    idle_closed = False

    # 세션 확인 또는 생성
    if payload.session_id:
        parsed_id = _parse_uuid(payload.session_id)
        if not parsed_id:
            return BadRequestResponse(
                message="세션 ID 형식이 올바르지 않습니다.",
                target=f"session_id={payload.session_id}"
            )

        session = await _get_session(db_manager, parsed_id)
        if not session:
            return NotFoundResponse(
                message="대화 세션을 찾을 수 없습니다.",
                target=f"session_id={payload.session_id}"
            )

        if not _can_access_session(user_info, session):
            return ForbiddenResponse(message="해당 대화 세션에 접근할 권한이 없습니다.")

        if session.status != ChatSessionStatus.ACTIVE:
            return BadRequestResponse(
                message="이미 종료된 대화 세션입니다. 새로운 대화를 시작해 주세요.",
                target=f"session_id={payload.session_id}"
            )

        # 미입력 자동 종료 처리 (KAI-REQ-039)
        if _is_idle_expired(session):
            now = util.get_now()
            query = (db_models.ChatSession.update(
                status=ChatSessionStatus.IDLE_CLOSED,
                updated_at=now,
            ).where(db_models.ChatSession.id == session.id))
            await db_manager.execute_query(query)

            logger.info(f"미입력으로 대화 세션이 자동 종료되었습니다. (session_id={session.id})")

            # 안내 문구는 응답 언어를 판정한 뒤에 채웁니다. (그래프 실행 후 아래에서 설정)
            idle_closed = True
            session = await _create_session(
                db_manager=db_manager,
                user_name=session.user_name,
                language=payload.language or session.language,
                profile=session.profile,
            )
    else:
        session = await _create_session(
            db_manager=db_manager,
            user_name=user_info.username,
            language=payload.language or ChatLanguage.AUTO,
        )

    # 명시 지정이 없으면 None으로 두고 그래프가 발화에서 언어를 감지하게 합니다. (KAI-REQ-029)
    language = _resolve_language(payload.language, session.language)

    # 최근 대화 이력 조회 (KAI-REQ-016)
    history = await _load_history(db_manager, session.id, config.chatbot.history_limit)

    # 개인정보 마스킹 (저장·검색·응답에 동일 규칙 적용)
    masking_rules = await load_active_rules(db_manager)
    user_message = apply_masking(payload.message, masking_rules)

    # 사용자 메시지 저장
    user_message_id = uuid4()
    query = db_models.ChatMessage.insert(
        id=user_message_id,
        session_id=session.id,
        role=ChatRole.USER,
        content=user_message,
        attachments=payload.attachments,
    )
    await db_manager.execute_query(query)

    # 그래프 실행 (의도 분류 → 검색 → 프롬프트 구성)
    try:
        state = await run_chat_graph(
            query=user_message,
            session_id=str(session.id),
            message_id=str(user_message_id),
            language=language,
            history=history,
            summary=session.summary,
            message_count=(session.message_count or 0) + 1,
            db_manager=db_manager,
            user_info=user_info,
            logger=logger,
        )
    except Exception:
        logger.exception("챗봇 그래프 실행 중 오류가 발생했습니다.")
        # 그래프가 통째로 실패해도 500으로 끊지 않고 안내 문구로 응답합니다.
        state = {
            "intent": ChatIntent.UNKNOWN,
            "sources": [],
            "answer": localized_message("fallback", language),
            "needs_generation": False,
            "unanswered_reason": UnansweredReason.MODEL_ERROR,
            "retrieval_attempted": False,
            "retrieval_latency_ms": 0,
            "language": language or Language.KO,
        }

    # 자동 감지 모드에서는 그래프가 판정한 언어가 이번 턴의 응답 언어입니다.
    language = state.get("language") or language or Language.KO
    if idle_closed:
        notice = localized_message("idle_closed", language)

    assistant_message_id = uuid4()
    intent: ChatIntent = state.get("intent") or ChatIntent.UNKNOWN
    sources: list[dict] = state.get("sources") or []
    outcome: dict[str, Any] = {"model_name": None, "unanswered_reason": None, "failed": False}

    # 비스트리밍 모드
    if not payload.stream:
        chunks: list[str] = []
        async for delta in _stream_answer(state, outcome, user_info, db_manager, logger):
            chunks.append(delta)
        answer = "".join(chunks) or localized_message("fallback", language)
        answer = apply_masking(answer, masking_rules)

        latency_ms = int((util.get_now() - started).total_seconds() * 1000)
        await _persist_turn(
            db_manager=db_manager,
            user_info=user_info,
            session=session,
            history=history,
            state=state,
            query_text=user_message,
            answer=answer,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            outcome=outcome,
            latency_ms=latency_ms,
            logger=logger,
        )

        unanswered_reason = outcome.get("unanswered_reason") or state.get("unanswered_reason")

        return ChatMessageResponse(
            session_id=session.id,
            message_id=assistant_message_id,
            user_message_id=user_message_id,
            answer=answer,
            detected_intent=intent,
            sources=[ChatSourceItem(**source) for source in sources],
            is_answered=unanswered_reason is None,
            unanswered_reason=unanswered_reason,
            notice=notice,
            latency_ms=latency_ms,
            created_at=util.get_now(),
        )

    # 스트리밍 모드 (SSE)
    async def event_generator() -> AsyncGenerator[dict, None]:
        chunks: list[str] = []
        persisted = False

        try:
            yield {
                "event": "session",
                "data": _dumps({
                    "session_id": str(session.id),
                    "user_message_id": str(user_message_id),
                    "message_id": str(assistant_message_id),
                    "detected_intent": intent.value if isinstance(intent, ChatIntent) else str(intent),
                    "language": language.value if isinstance(language, Language) else str(language),
                    "notice": notice,
                })
            }

            if sources:
                yield {"event": "sources", "data": _dumps({"sources": sources})}

            async for delta in _stream_answer(state, outcome, user_info, db_manager, logger):
                chunks.append(delta)
                yield {"event": "delta", "data": _dumps({"content": delta})}

            if outcome.get("failed"):
                yield {
                    "event": "error",
                    "data": _dumps({"message": "응답 생성 중 오류가 발생하여 안내 문구로 대체되었습니다."})
                }

            answer = "".join(chunks) or localized_message("fallback", language)
            answer = apply_masking(answer, masking_rules)
            latency_ms = int((util.get_now() - started).total_seconds() * 1000)

            await _persist_turn(
                db_manager=db_manager,
                user_info=user_info,
                session=session,
                history=history,
                state=state,
                query_text=user_message,
                answer=answer,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                outcome=outcome,
                latency_ms=latency_ms,
                logger=logger,
            )
            persisted = True

            unanswered_reason = outcome.get("unanswered_reason") or state.get("unanswered_reason")
            yield {
                "event": "done",
                "data": _dumps({
                    "session_id": str(session.id),
                    "message_id": str(assistant_message_id),
                    "detected_intent": intent.value if isinstance(intent, ChatIntent) else str(intent),
                    "is_answered": unanswered_reason is None,
                    "unanswered_reason": unanswered_reason.value if unanswered_reason else None,
                    "latency_ms": latency_ms,
                })
            }
        finally:
            # 클라이언트가 중간에 연결을 끊어도 지금까지 생성된 응답은 이력에 남깁니다.
            if not persisted:
                try:
                    await _persist_turn(
                        db_manager=db_manager,
                        user_info=user_info,
                        session=session,
                        history=history,
                        state=state,
                        query_text=user_message,
                        answer=apply_masking(
                            "".join(chunks) or localized_message("fallback", language), masking_rules),
                        user_message_id=user_message_id,
                        assistant_message_id=assistant_message_id,
                        outcome=outcome,
                        latency_ms=int((util.get_now() - started).total_seconds() * 1000),
                        logger=logger,
                    )
                except Exception:
                    logger.exception("중단된 대화 응답을 저장하지 못했습니다.")

    return EventSourceResponse(
        event_generator(),
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
