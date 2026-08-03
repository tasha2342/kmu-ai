"""계명대 챗봇 주기 작업 (KAI-REQ-014 / KAI-REQ-039)

수집 작업 API(`app/api/v1/endpoints/ingestion.py`)와 스케줄러 잡이 동일한 재색인 로직을
쓰도록, FAQ 재색인의 핵심 함수를 이 모듈에 모아 두었습니다.
API 엔드포인트가 이 모듈을 임포트하는 단방향 구조이며, 이 모듈은 엔드포인트/라우터를
임포트하지 않습니다. (순환 임포트 방지)

FAQ 벡터는 PostgreSQL(pgvector + HNSW)에 저장되며, 색인 자체는 `app/utils/faq_service.py`가
담당합니다. 이 모듈은 "무엇을 언제 다시 색인할지"와 작업 이력 기록만 책임집니다.

**시스템 사용자 한계**
스케줄러 잡에는 HTTP 요청 컨텍스트가 없어 실제 사용자 토큰(`TokenUserInfo.token`)을 얻을 수 없습니다.
그래서 `SYSTEM_USER_INFO`(sub/username = "system", token은 기본값 "-")를 사용합니다.
LOCAL provider 모델은 `user_info.token`을 그대로 내부 추론 서버 호출에 사용하므로,
LOCAL provider 임베딩 모델이 사용자 토큰 검증을 요구하도록 바뀌면 스케줄러 재색인은 실패합니다.
(그 경우 관리자가 `POST /ingestion/job/run`으로 수동 실행해야 합니다.)
"""

from logging import Logger

from datetime import timedelta

from typing import Any, Optional

from uuid import UUID, uuid4

from app.config import config, env
from app.models.auth import TokenUserInfo
from app.models.enum import (
    SourceType,
    IngestionStatus,
    IngestionItemStatus,
    VectorStatus,
    FaqStatus,
    ChatSessionStatus,
    ChatRole,
)
from app.scheduler.jobs.base import create_job
from app.utils.database import DatabaseManager, get_db_manager
from app.utils.faq_service import (
    ensure_faq_collection,
    get_embedding_model,
    sync_faq_item,
)
from app.utils.regulation_ingest import (
    REGULATION_SOURCE_TABLE,
    RegulationIngestResult,
    ensure_regulation_collection,
    ingest_regulation_directory,
    list_regulation_files,
)
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.common as util


logger, job_decorator = create_job("chatbot")


FAQ_SOURCE_TABLE = "faq_items"
"""FAQ 재색인 시 `ingestion_job_items.source_table`에 기록하는 원천 테이블명"""

BATCH_SIZE = 100
"""항목 기록·세션 정리 시 한 번에 처리하는 행 수"""

SYSTEM_USER_INFO = TokenUserInfo(sub="system", username="system")
"""스케줄러 잡 전용 시스템 사용자 정보. (요청 컨텍스트가 없어 실제 사용자 토큰을 쓸 수 없습니다.)"""


# ===== FAQ 재색인 (KAI-REQ-014) =====

async def select_faq_targets(
    db_manager: DatabaseManager,
    only_stale: bool = True,
) -> list[db_items.FaqItem]:
    """FAQ 재색인 대상 목록을 조회합니다.

    `only_stale`이 `True`이면 색인 상태가 `indexed`가 아닌 공개 FAQ만 고릅니다.
    즉 색인 상태가 `stale`/`pending`/`failed`인 FAQ와, 색인 레코드가 아예 없는
    (= 한 번도 색인되지 않은) FAQ가 모두 대상이 됩니다.
    임시 저장/보관 FAQ는 챗봇 응답 근거가 되면 안 되므로 대상에서 제외합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        only_stale (bool): 재색인이 필요한 FAQ만 대상으로 할지 여부 (Default: True)

    Returns:
        list[db_items.FaqItem]: 재색인 대상 FAQ 목록
    """

    query = (db_models.FaqItem.select()
             .where(db_models.FaqItem.status == FaqStatus.PUBLISHED.value)
             .order_by(db_models.FaqItem.created_at.asc()))

    if only_stale:
        indexed_query = (db_models.FaqEmbedding
                         .select(db_models.FaqEmbedding.faq_id)
                         .where(db_models.FaqEmbedding.vector_status == VectorStatus.INDEXED.value))
        query = query.where(db_models.FaqItem.id.not_in(indexed_query))

    return await db_manager.select_items(query, timeout=30.0)

async def reap_stale_jobs(
    db_manager: DatabaseManager,
    source_type: SourceType,
) -> int:
    """타임아웃을 넘겨 `running`으로 방치된 수집 작업을 실패로 회수합니다.

    수집 작업은 `BackgroundTasks`로 워커 프로세스 안에서 돌기 때문에, 실행 도중 배포·재시작 등으로
    프로세스가 죽으면 작업이 영원히 `running`으로 남습니다. 그러면 중복 방지 로직에 걸려
    API는 409, 스케줄러는 skip이 되어 재색인이 영구히 막힙니다. 이를 자동으로 푸는 안전장치입니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        source_type (SourceType): 원천 유형

    Returns:
        int: 회수한 작업 수
    """

    now = util.get_now()
    deadline = now - timedelta(minutes=config.chatbot.ingestion_job_timeout_minutes)

    query = (db_models.IngestionJob.update(
        status=IngestionStatus.FAILED.value,
        error_message=(
            f"작업이 {config.chatbot.ingestion_job_timeout_minutes}분을 넘겨 응답하지 않아 실패로 처리했습니다. "
            "(실행 중 프로세스가 종료되었을 수 있습니다.)"
        ),
        ended_at=now,
    ).where(db_models.IngestionJob.source_type == source_type.value)
     .where(db_models.IngestionJob.status == IngestionStatus.RUNNING.value)
     .where(db_models.IngestionJob.started_at < deadline))
    reaped = await db_manager.execute_query(query)

    if reaped:
        logger.warning(f"응답 없는 수집 작업을 실패로 회수했습니다. (source_type={source_type.value}, count={reaped})")
    return reaped or 0

async def get_running_job(
    db_manager: DatabaseManager,
    source_type: SourceType,
) -> Optional[db_items.IngestionJob]:
    """진행 중인 수집 작업을 조회합니다.

    스케줄러와 관리자 수동 실행이 동시에 같은 FAQ를 색인하지 않도록 확인하는 용도입니다.
    조회 전에 타임아웃을 넘긴 작업을 먼저 회수하므로, 죽은 작업이 재색인을 영구히 막지 않습니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        source_type (SourceType): 원천 유형

    Returns:
        Optional[db_items.IngestionJob]: 진행 중인 수집 작업 또는 None
    """

    await reap_stale_jobs(db_manager, source_type)

    query = (db_models.IngestionJob.select()
             .where(db_models.IngestionJob.source_type == source_type.value)
             .where(db_models.IngestionJob.status == IngestionStatus.RUNNING.value)
             .order_by(db_models.IngestionJob.started_at.desc()))
    return await db_manager.select_item(query)

async def create_ingestion_job(
    db_manager: DatabaseManager,
    source_type: SourceType,
) -> UUID:
    """수집 작업을 `running` 상태로 생성합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        source_type (SourceType): 원천 유형

    Returns:
        UUID: 생성된 수집 작업 ID
    """

    job_id = uuid4()
    query = db_models.IngestionJob.insert(
        id=job_id,
        source_type=source_type.value,
        status=IngestionStatus.RUNNING.value,
        started_at=util.get_now(),
    )
    await db_manager.execute_query(query)
    return job_id

async def finish_ingestion_job(
    db_manager: DatabaseManager,
    job_id: UUID,
    status: IngestionStatus,
    success_count: int = 0,
    failed_count: int = 0,
    error_message: Optional[str] = None,
    total_count: Optional[int] = None,
):
    """수집 작업을 종료 상태로 마감합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        job_id (UUID): 수집 작업 ID
        status (IngestionStatus): 마감 상태 (completed/failed)
        success_count (int): 성공 항목 수 (Default: 0)
        failed_count (int): 실패 항목 수 (Default: 0)
        error_message (Optional[str]): 작업 단위 오류 메시지
        total_count (Optional[int]): 전체 항목 수 (None이면 갱신하지 않습니다.)
    """

    values: dict[str, Any] = {
        "status": status.value,
        "success_count": success_count,
        "failed_count": failed_count,
        "error_message": error_message[:1024] if error_message else None,
        "ended_at": util.get_now(),
    }
    if total_count is not None:
        values["total_count"] = total_count

    query = (db_models.IngestionJob.update(**values)
             .where(db_models.IngestionJob.id == job_id))
    await db_manager.execute_query(query)

async def insert_job_items(db_manager: DatabaseManager, rows: list[dict[str, Any]]):
    """수집 작업 항목을 일괄 기록합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        rows (list[dict[str, Any]]): 항목 행 목록
    """

    if not rows:
        return

    query = db_models.IngestionJobItem.insert_many(rows)
    await db_manager.execute_query(query, timeout=30.0)

async def run_faq_ingestion(
    db_manager: DatabaseManager,
    job_id: UUID,
    user_info: TokenUserInfo,
    faqs: list[db_items.FaqItem],
    force: bool = False,
    request_logger: Optional[Logger] = None,
) -> tuple[int, int, int, int]:
    """FAQ 목록을 재색인하고 수집 작업/항목을 기록합니다. (KAI-REQ-014)

    임베딩 호출이나 벡터 저장이 실패해도 작업 전체를 중단하지 않고 해당 항목만
    `failed`로 기록한 뒤 다음 항목을 계속 처리합니다.
    컬렉션·임베딩 모델을 아예 준비하지 못한 경우에만 작업 단위 `failed`로 마감합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        job_id (UUID): 수집 작업 ID
        user_info (TokenUserInfo): 사용자 정보 (스케줄러 실행 시 `SYSTEM_USER_INFO`)
        faqs (list[db_items.FaqItem]): 재색인할 FAQ 목록
        force (bool): 원문 변경이 없어도 강제로 재색인할지 여부 (Default: False)
        request_logger (Optional[Logger]): 사용할 로거 (None이면 잡 로거를 사용합니다.)

    Returns:
        tuple[int, int, int, int]: (전체 수, 성공 수, 실패 수, 건너뛴 수)
    """

    log = request_logger or logger
    total_count = len(faqs)
    success_count = 0
    failed_count = 0
    skipped_count = 0

    # 전체 항목 수 선반영 (진행 상황 조회용)
    query = (db_models.IngestionJob.update(total_count=total_count)
             .where(db_models.IngestionJob.id == job_id))
    await db_manager.execute_query(query)

    # FAQ 지식베이스 컬렉션 준비
    try:
        collection, error_message = await ensure_faq_collection(
            db_manager=db_manager,
            embedding_model_name=config.chatbot.embedding_model,
            user_info=user_info,
        )
    except Exception as exc:
        log.exception(f"FAQ 지식베이스 컬렉션 준비 중 오류가 발생했습니다. (job_id={job_id})")
        collection, error_message = None, f"FAQ 지식베이스 컬렉션을 준비할 수 없습니다. ({exc})"

    if not collection:
        await finish_ingestion_job(
            db_manager=db_manager,
            job_id=job_id,
            status=IngestionStatus.FAILED,
            error_message=error_message or "FAQ 지식베이스 컬렉션을 준비할 수 없습니다.",
        )
        return total_count, 0, 0, 0

    # 임베딩 모델 확인
    model = await get_embedding_model(db_manager, collection)
    if not model:
        await finish_ingestion_job(
            db_manager=db_manager,
            job_id=job_id,
            status=IngestionStatus.FAILED,
            error_message=f"FAQ 지식베이스의 임베딩 모델을 사용할 수 없습니다. (embedding_model={collection.embedding_model})",
        )
        return total_count, 0, 0, 0

    # FAQ 건별 색인
    rows: list[dict[str, Any]] = []
    for faq in faqs:
        try:
            result = await sync_faq_item(
                db_manager=db_manager,
                collection=collection,
                model=model,
                user_info=user_info,
                faq=faq,
                force=force,
            )
            if result.vector_status == VectorStatus.FAILED:
                item_status = IngestionItemStatus.FAILED
                item_error = result.error_message or "FAQ 색인에 실패했습니다."
                failed_count += 1
            elif result.skipped:
                item_status = IngestionItemStatus.SKIPPED
                item_error = None
                skipped_count += 1
            else:
                item_status = IngestionItemStatus.SUCCESS
                item_error = None
                success_count += 1
        except Exception as exc:
            # 임베딩·벡터 저장 오류로 한 건이 실패해도 작업 전체는 계속 진행합니다.
            log.exception(f"FAQ 색인 중 오류가 발생했습니다. (job_id={job_id}, faq_id={faq.id})")
            item_status = IngestionItemStatus.FAILED
            item_error = str(exc)
            failed_count += 1

        rows.append({
            "id": uuid4(),
            "job_id": job_id,
            "source_table": FAQ_SOURCE_TABLE,
            "source_id": faq.id,
            "status": item_status.value,
            "error_message": item_error,
            "created_at": util.get_now(),
        })

        if len(rows) >= BATCH_SIZE:
            await insert_job_items(db_manager, rows)
            rows = []

    await insert_job_items(db_manager, rows)

    # 집계 후 마감 (전량 실패한 경우에만 작업 자체를 실패로 봅니다.)
    if failed_count and not success_count and not skipped_count:
        job_status = IngestionStatus.FAILED
        job_error = f"{failed_count}건의 항목 색인에 모두 실패했습니다."
    else:
        job_status = IngestionStatus.COMPLETED
        job_error = f"{failed_count}건의 항목 색인에 실패했습니다." if failed_count else None

    await finish_ingestion_job(
        db_manager=db_manager,
        job_id=job_id,
        status=job_status,
        success_count=success_count,
        failed_count=failed_count,
        error_message=job_error,
        total_count=total_count,
    )

    log.debug(
        "FAQ 재색인이 완료되었습니다. "
        f"(job_id={job_id}, total={total_count}, success={success_count}, failed={failed_count}, skipped={skipped_count})"
    )
    return total_count, success_count, failed_count, skipped_count


# ===== 학칙·규정 재색인 (KAI-REQ-014) =====

async def run_regulation_ingestion(
    db_manager: DatabaseManager,
    job_id: UUID,
    user_info: TokenUserInfo,
    force: bool = False,
    request_logger: Optional[Logger] = None,
) -> tuple[int, int, int, int]:
    """`resources/regulations/`의 학칙·규정 HWP를 재색인합니다. (KAI-REQ-014)

    FAQ 재색인(`run_faq_ingestion`)과 같은 구조입니다.
    문서 1건이 실패해도 해당 항목만 `failed`로 남기고 나머지를 계속 처리하며,
    컬렉션·임베딩 모델을 준비하지 못한 경우에만 작업 단위로 실패 마감합니다.

    FAQ와 달리 대상 수를 미리 알 수 있지만(디렉터리의 HWP 파일 수), 적재가
    문서 단위로 진행되므로 항목은 문서 1건이 끝날 때마다 기록합니다.
    수집 작업 상세 조회로 진행 상황을 실시간에 가깝게 볼 수 있게 하기 위함입니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        job_id (UUID): 수집 작업 ID
        user_info (TokenUserInfo): 사용자 정보
        force (bool): 원문 변경이 없어도 강제로 재색인할지 여부 (Default: False)
        request_logger (Optional[Logger]): 사용할 로거

    Returns:
        tuple[int, int, int, int]: (전체 수, 성공 수, 실패 수, 건너뛴 수)
    """

    log = request_logger or logger

    # 전체 항목 수 선반영 (진행 상황 조회용)
    query = (db_models.IngestionJob.update(total_count=len(list_regulation_files()))
             .where(db_models.IngestionJob.id == job_id))
    await db_manager.execute_query(query)

    # 학칙·규정 지식베이스 컬렉션 준비
    try:
        collection, error_message = await ensure_regulation_collection(
            db_manager=db_manager,
            embedding_model_name=config.chatbot.embedding_model,
            user_info=user_info,
        )
    except Exception as exc:
        log.exception(f"학칙·규정 지식베이스 컬렉션 준비 중 오류가 발생했습니다. (job_id={job_id})")
        collection, error_message = None, f"학칙·규정 지식베이스 컬렉션을 준비할 수 없습니다. ({exc})"

    if not collection:
        await finish_ingestion_job(
            db_manager=db_manager,
            job_id=job_id,
            status=IngestionStatus.FAILED,
            error_message=error_message or "학칙·규정 지식베이스 컬렉션을 준비할 수 없습니다.",
        )
        return 0, 0, 0, 0

    # 임베딩 모델 확인
    model = await get_embedding_model(db_manager, collection)
    if not model:
        await finish_ingestion_job(
            db_manager=db_manager,
            job_id=job_id,
            status=IngestionStatus.FAILED,
            error_message=(
                "학칙·규정 지식베이스의 임베딩 모델을 사용할 수 없습니다. "
                f"(embedding_model={collection.embedding_model})"
            ),
        )
        return 0, 0, 0, 0

    rows: list[dict[str, Any]] = []

    async def _record(result: RegulationIngestResult):
        """문서 1건의 적재 결과를 수집 작업 항목으로 기록합니다."""

        if result.failed:
            item_status = IngestionItemStatus.FAILED
        elif result.skipped:
            item_status = IngestionItemStatus.SKIPPED
        else:
            item_status = IngestionItemStatus.SUCCESS

        rows.append({
            "id": uuid4(),
            "job_id": job_id,
            "source_table": REGULATION_SOURCE_TABLE,
            "source_id": result.source_id,
            "status": item_status.value,
            "error_message": result.error_message,
            "created_at": util.get_now(),
        })

        if len(rows) >= BATCH_SIZE:
            await insert_job_items(db_manager, rows)
            rows.clear()

    results = await ingest_regulation_directory(
        db_manager=db_manager,
        model=model,
        user_info=user_info,
        force=force,
        on_result=_record,
        request_logger=log,
    )

    await insert_job_items(db_manager, rows)
    rows.clear()

    total_count = len(results)
    success_count = sum(1 for result in results if not result.failed and not result.skipped)
    failed_count = sum(1 for result in results if result.failed)
    skipped_count = sum(1 for result in results if result.skipped)

    if not total_count:
        job_status = IngestionStatus.FAILED
        job_error = "적재할 학칙·규정 HWP 파일을 찾지 못했습니다. (resources/regulations)"
    elif failed_count and not success_count and not skipped_count:
        job_status = IngestionStatus.FAILED
        job_error = f"{failed_count}건의 문서 적재에 모두 실패했습니다."
    else:
        job_status = IngestionStatus.COMPLETED
        job_error = f"{failed_count}건의 문서 적재에 실패했습니다." if failed_count else None

    await finish_ingestion_job(
        db_manager=db_manager,
        job_id=job_id,
        status=job_status,
        success_count=success_count,
        failed_count=failed_count,
        error_message=job_error,
        total_count=total_count,
    )

    log.debug(
        "학칙·규정 재색인이 완료되었습니다. "
        f"(job_id={job_id}, total={total_count}, success={success_count}, "
        f"failed={failed_count}, skipped={skipped_count})"
    )
    return total_count, success_count, failed_count, skipped_count


@job_decorator
async def sync_stale_faq_job():
    """재색인이 필요한 FAQ를 주기적으로 동기화합니다. (KAI-REQ-014)

    색인 상태가 `stale`/`pending`/`failed`이거나 한 번도 색인되지 않은 공개 FAQ가 대상이며,
    수집 작업 API의 `only_stale=true`와 완전히 동일한 대상 선정 로직(`select_faq_targets`)을 사용합니다.

    대상이 없으면 수집 작업 이력을 남기지 않고 즉시 종료하며,
    이미 진행 중인 FAQ 수집 작업이 있으면 중복 색인을 피하기 위해 건너뜁니다.

    Note:
        요청 컨텍스트가 없어 실제 사용자 토큰을 쓸 수 없으므로 `SYSTEM_USER_INFO`로 실행됩니다.
        (LOCAL provider 모델은 `user_info.token`을 사용하므로 기본값 `"-"`가 그대로 전달됩니다.)
    """

    db_manager = await get_db_manager()

    # 중복 실행 방지
    running_job = await get_running_job(db_manager, SourceType.FAQ)
    if running_job:
        logger.info(f"이미 진행 중인 FAQ 수집 작업이 있어 건너뜁니다. (job_id={running_job.id})")
        return

    faqs = await select_faq_targets(db_manager, only_stale=True)
    if not faqs:
        logger.debug("재색인이 필요한 FAQ가 없습니다.")
        return

    job_id = await create_ingestion_job(db_manager, SourceType.FAQ)
    logger.info(f"FAQ 재색인 작업을 시작합니다. (job_id={job_id}, target_count={len(faqs)})")

    try:
        await run_faq_ingestion(
            db_manager=db_manager,
            job_id=job_id,
            user_info=SYSTEM_USER_INFO,
            faqs=faqs,
            force=False,
        )
    except Exception as exc:
        logger.exception(f"FAQ 재색인 작업 중 오류가 발생했습니다. (job_id={job_id})")
        await finish_ingestion_job(
            db_manager=db_manager,
            job_id=job_id,
            status=IngestionStatus.FAILED,
            error_message=f"FAQ 재색인 작업 중 오류가 발생했습니다. ({exc})",
        )


# ===== 미입력 세션 자동 종료 (KAI-REQ-039) =====

@job_decorator
async def close_idle_sessions_job():
    """일정 시간 입력이 없는 대화 세션을 자동으로 종료합니다. (KAI-REQ-039)

    `SESSION_IDLE_TIMEOUT_MINUTES`(분)를 넘겨 마지막 활동이 없는
    `active` 세션을 `idle_closed`로 전환하고, 해당 세션에
    `config.chatbot.messages.idle_closed` 문구를 담은 `system` 역할 메시지를 1건 남깁니다.

    환경 변수 `ENABLE_SESSION_IDLE_TIMEOUT`이 false면 아무 것도 하지 않습니다.
    (기동 시 등록 자체를 건너뛰지만, 재등록 등으로 실행되더라도 안전하도록 여기서도 확인합니다)

    안내 메시지를 먼저 기록한 뒤 세션 상태를 바꾸므로, 중간에 실패하더라도
    "종료됐는데 안내가 없는" 세션은 생기지 않습니다.
    (드물게 안내만 남고 다음 주기에 다시 처리될 수는 있습니다.)

    Note:
        요청 컨텍스트가 없어 사용자 토큰을 쓸 수 없지만, 이 작업은 DB만 사용하므로 영향이 없습니다.
    """

    if not env.ENABLE_SESSION_IDLE_TIMEOUT:
        logger.debug("미입력 세션 자동 종료가 비활성화되어 있어 건너뜁니다. (ENABLE_SESSION_IDLE_TIMEOUT=false)")
        return

    timeout_minutes = env.SESSION_IDLE_TIMEOUT_MINUTES
    if timeout_minutes <= 0:
        logger.debug(f"자동 종료 기준 시간이 유효하지 않아 건너뜁니다. (SESSION_IDLE_TIMEOUT_MINUTES={timeout_minutes})")
        return

    db_manager = await get_db_manager()

    threshold = util.get_now() - timedelta(minutes=timeout_minutes)

    query = (db_models.ChatSession.select()
             .where(db_models.ChatSession.status == ChatSessionStatus.ACTIVE.value)
             .where(db_models.ChatSession.last_active_at < threshold)
             .order_by(db_models.ChatSession.last_active_at.asc()))
    sessions: list[db_items.ChatSession] = await db_manager.select_items(query, timeout=30.0)

    if not sessions:
        logger.debug(f"자동 종료할 세션이 없습니다. (timeout_minutes={timeout_minutes})")
        return

    closed_count = 0
    for index in range(0, len(sessions), BATCH_SIZE):
        batch = sessions[index:index + BATCH_SIZE]
        now = util.get_now()

        # 종료 안내 메시지 기록
        message_rows = [{
            "id": uuid4(),
            "session_id": session.id,
            "role": ChatRole.SYSTEM.value,
            "content": config.chatbot.messages.idle_closed,
            "is_answered": True,
            "created_at": now,
        } for session in batch]
        insert_query = db_models.ChatMessage.insert_many(message_rows)
        await db_manager.execute_query(insert_query, timeout=30.0)

        # 세션 상태 전환
        update_query = (db_models.ChatSession.update(
            status=ChatSessionStatus.IDLE_CLOSED.value,
            message_count=db_models.ChatSession.message_count + 1,
            updated_at=now,
        ).where(db_models.ChatSession.id.in_([session.id for session in batch])))
        await db_manager.execute_query(update_query, timeout=30.0)

        closed_count += len(batch)

    logger.info(f"미입력 세션이 자동 종료되었습니다. (count={closed_count}, timeout_minutes={timeout_minutes})")
