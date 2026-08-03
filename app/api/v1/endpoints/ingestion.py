from logging import Logger

from typing import Optional

from uuid import UUID

from peewee import fn

from fastapi import APIRouter, Depends, BackgroundTasks, Query, Path

from app.config import config
from app.exceptions.api_exception import (
    DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
)
from app.payloads.v1.ingestion import RunIngestionPayload
from app.responses.base import BaseMessageResponse, BaseListResponse
from app.responses.v1.ingestion import (
    IngestionJobInfo,
    IngestionJobItemSummary,
    IngestionJobDetailResponse,
    IngestionJobItemInfo,
    RunIngestionResponse,
)
from app.responses.exception import (
    BadRequestResponse,
    NotFoundResponse,
    ConflictResponse,
)
from app.models.auth import TokenUserInfo
from app.models.api.exception import (
    BadRequestError,
    NotFoundError,
    ConflictError,
)
from app.models.enum import (
    SourceType,
    IngestionStatus,
    IngestionItemStatus,
)
from app.scheduler.jobs.chatbot import (
    select_faq_targets,
    get_running_job,
    create_ingestion_job,
    finish_ingestion_job,
    run_faq_ingestion,
    run_regulation_ingestion,
)
from app.utils.logger import get_api_logger
from app.utils.database import DatabaseManager, get_db_manager
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.auth as auth


router = APIRouter()


SUPPORTED_SOURCE_TYPES = [SourceType.FAQ, SourceType.REGULATION]
"""현재 재색인을 지원하는 원천 유형

- `faq`: `faq_items` 테이블이 원천
- `regulation`: 리포지토리에 동봉된 `resources/regulations/*.hwp` 181개가 원천

`notice`/`document`는 학내 원천 데이터 연계(API·DB)가 제공되지 않아 여전히 지원하지 않습니다.
"""

START_MESSAGES = {
    SourceType.FAQ: "FAQ 재색인 작업이 시작되었습니다.",
    SourceType.REGULATION: "학칙·규정 재색인 작업이 시작되었습니다.",
}
"""원천 유형별 실행 응답 메시지"""


async def _get_item_summary(db_manager: DatabaseManager, job_id: UUID) -> IngestionJobItemSummary:
    """수집 작업의 처리 상태별 항목 수를 집계합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        job_id (UUID): 수집 작업 ID

    Returns:
        IngestionJobItemSummary: 처리 상태별 항목 수 요약
    """

    query = (db_models.IngestionJobItem
             .select(
                 db_models.IngestionJobItem.status,
                 fn.COUNT(db_models.IngestionJobItem.id).alias("count")
             )
             .where(db_models.IngestionJobItem.job_id == job_id)
             .group_by(db_models.IngestionJobItem.status))
    rows = await db_manager.execute_query(query)

    counts = {str(row.status): row.count for row in rows}
    return IngestionJobItemSummary(
        total=sum(counts.values()),
        success=counts.get(IngestionItemStatus.SUCCESS.value, 0),
        failed=counts.get(IngestionItemStatus.FAILED.value, 0),
        skipped=counts.get(IngestionItemStatus.SKIPPED.value, 0),
        pending=counts.get(IngestionItemStatus.PENDING.value, 0),
    )


@router.get("/job/list", summary="수집 작업 목록 조회",
    description=(
        "지식베이스 수집(재색인) 작업 이력을 페이징 처리하여 조회합니다.  \n"
        "최근 시작한 작업이 먼저 오도록 시작 일시 내림차순으로 정렬됩니다."
    ),
    responses={
        200: {"description": "수집 작업 목록을 반환합니다.", "model": BaseListResponse[IngestionJobInfo]},
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_ingestion_job_list(
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
    source_type: Optional[SourceType] = Query(
        None,
        description="원천 유형으로 필터링합니다.",
        examples=list(SourceType)
    ),
    status: Optional[IngestionStatus] = Query(
        None,
        description="작업 상태로 필터링합니다.",
        examples=list(IngestionStatus)
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 기본 쿼리
    query = db_models.IngestionJob.select()
    count_query = db_models.IngestionJob.select(fn.COUNT(db_models.IngestionJob.id).alias("count"))

    # 필터 조건
    conditions = []

    if source_type:
        conditions.append(db_models.IngestionJob.source_type == source_type.value)
    if status:
        conditions.append(db_models.IngestionJob.status == status.value)

    # 조건 적용
    for condition in conditions:
        query = query.where(condition)
        count_query = count_query.where(condition)

    # 정렬 (시작 일시 내림차순)
    query = query.order_by(db_models.IngestionJob.started_at.desc())

    # 페이징
    if count > 0:
        offset = (page - 1) * count
        query = query.offset(offset).limit(count)

    # 총 개수 조회
    count_result = await db_manager.execute_query(count_query)
    total_count = count_result[0].count if count_result else 0

    # 목록 조회
    jobs: list[db_items.IngestionJob] = await db_manager.select_items(query)

    # 총 페이지 수
    if count > 0:
        total_pages = (total_count + count - 1) // count if total_count > 0 else 1
    else:
        total_pages = 1

    return BaseListResponse[IngestionJobInfo](
        total_pages=total_pages,
        total_count=total_count,
        items=[IngestionJobInfo(**job.model_dump()) for job in jobs]
    )

@router.post("/job/run", summary="수집 작업 실행",
    description=(
        "지식베이스 재색인 작업을 실행합니다. (KAI-REQ-014)  \n"
        "색인은 백그라운드에서 진행되며, 요청은 생성된 수집 작업 ID를 즉시 반환합니다.  \n"
        "진행 상태는 `GET /ingestion/job/{job_id}`로 확인합니다.  \n"
        "  \n"
        "**`source_type=faq`**  \n"
        "`only_stale=true`(기본값)이면 색인 상태가 `stale`/`pending`/`failed`이거나 "
        "색인 레코드가 없는 공개(`published`) FAQ만 대상으로 합니다.  \n"
        "스케줄러의 FAQ 자동 재색인 작업과 동일한 대상 선정 로직을 사용합니다.  \n"
        "  \n"
        "**`source_type=regulation`**  \n"
        "리포지토리에 동봉된 `resources/regulations/*.hwp`(181개)를 추출·청킹·임베딩해 "
        "`kmu_regulations` 컬렉션에 적재합니다.  \n"
        "본문은 `hwp5txt`, 표는 `hwp5html`로 추출하며 문서메타·조항·표 청크를 만듭니다.  \n"
        "원문과 청킹 결과가 지난번과 같은 문서는 건너뜁니다. "
        "`force=true` 또는 `only_stale=false`이면 변경이 없어도 전량 다시 임베딩합니다.  \n"
        "문서 1건이 실패해도 나머지 문서는 계속 적재되며, 실패 사유는 작업 항목에 남습니다.  \n"
        "  \n"
        "**공지사항·업로드 문서는 아직 지원하지 않습니다.**  \n"
        "학내 원천 데이터 연계(API·DB)가 제공되지 않아 수집할 원천이 없으므로 `400`을 반환합니다."
    ),
    responses={
        200: {"description": "생성된 수집 작업 정보를 반환합니다.", "model": RunIngestionResponse},
        400: {
            "description": "잘못된 요청입니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "unsupported_source_type": {
                            "summary": "지원하지 않는 원천 유형",
                            "value": {
                                "message": "아직 지원하지 않는 원천 유형입니다.",
                                "target": "source_type={source_type}"
                            }
                        }
                    }
                }
            }
        },
        409: {
            "description": "이미 진행 중인 작업이 있습니다.",
            "model": ConflictError,
            "content": {
                "application/json": {
                    "examples": {
                        "already_running": {
                            "summary": "진행 중인 수집 작업 존재",
                            "value": {
                                "message": "이미 진행 중인 수집 작업이 있습니다.",
                                "target": "job_id={job_id}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def run_ingestion_job(
    payload: RunIngestionPayload,
    background_tasks: BackgroundTasks,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 지원 원천 유형 확인
    if payload.source_type not in SUPPORTED_SOURCE_TYPES:
        return BadRequestResponse(
            message=(
                "아직 지원하지 않는 원천 유형입니다. "
                "공지사항·업로드 문서는 학내 원천 데이터 연계가 제공되지 않아 수집할 수 없습니다. "
                "현재는 FAQ와 학칙·규정만 재색인할 수 있습니다."
            ),
            target=f"source_type={payload.source_type.value}"
        )

    # 중복 실행 확인
    running_job = await get_running_job(db_manager, payload.source_type)
    if running_job:
        return ConflictResponse(
            message="이미 진행 중인 수집 작업이 있습니다. 작업이 끝난 뒤 다시 시도해주세요.",
            target=f"job_id={running_job.id}"
        )

    # 수집 작업 생성 (running)
    job_id = await create_ingestion_job(db_manager, payload.source_type)

    async def _process_ingestion():
        try:
            if payload.source_type == SourceType.REGULATION:
                # 학칙·규정은 DB가 아니라 `resources/regulations/`의 HWP 파일이 원천이라
                # 대상 선정 없이 디렉터리 전체를 순회합니다.
                # (`only_stale`은 원문 해시가 그대로인 문서를 건너뛰는 데 쓰입니다.)
                await run_regulation_ingestion(
                    db_manager=db_manager,
                    job_id=job_id,
                    user_info=user_info,
                    force=payload.force or not payload.only_stale,
                    request_logger=logger,
                )
                return

            # 대상 FAQ 조회
            faqs = await select_faq_targets(db_manager, only_stale=payload.only_stale)

            await run_faq_ingestion(
                db_manager=db_manager,
                job_id=job_id,
                user_info=user_info,
                faqs=faqs,
                force=payload.force,
                request_logger=logger,
            )
        except Exception as exc:
            logger.exception(f"수집 작업 처리 중 오류가 발생했습니다. (job_id={job_id})")
            await finish_ingestion_job(
                db_manager=db_manager,
                job_id=job_id,
                status=IngestionStatus.FAILED,
                error_message=f"수집 작업 처리 중 오류가 발생했습니다. ({exc})",
            )

    # 항목 수가 많으면 요청이 타임아웃되므로 백그라운드에서 처리
    background_tasks.add_task(_process_ingestion)

    logger.debug(f"수집 작업이 시작되었습니다. (job_id={job_id}, source_type={payload.source_type.value})")

    return RunIngestionResponse(
        message=START_MESSAGES.get(payload.source_type, "재색인 작업이 시작되었습니다."),
        id=job_id,
        source_type=payload.source_type,
        status=IngestionStatus.RUNNING
    )

@router.get("/job/{job_id}", summary="수집 작업 상세 조회",
    description=(
        "수집 작업 상세 정보와 처리 상태별 항목 수 요약을 조회합니다.  \n"
        "`success_count`에는 원문 변경이 없어 건너뛴(`skipped`) 항목이 포함되지 않습니다."
    ),
    responses={
        200: {"description": "수집 작업 상세 정보를 반환합니다.", "model": IngestionJobDetailResponse},
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 수집 작업",
                            "value": {
                                "message": "수집 작업을 찾을 수 없습니다.",
                                "target": "job_id={job_id}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_ingestion_job(
    job_id: UUID = Path(
        ...,
        description="조회할 수집 작업 ID입니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 수집 작업 조회
    query = (db_models.IngestionJob.select()
             .where(db_models.IngestionJob.id == job_id))
    job: Optional[db_items.IngestionJob] = await db_manager.select_item(query)
    if not job:
        return NotFoundResponse(
            message="수집 작업을 찾을 수 없습니다.",
            target=f"job_id={job_id}"
        )

    # 항목 요약 집계
    item_summary = await _get_item_summary(db_manager, job_id)

    return IngestionJobDetailResponse(
        **job.model_dump(),
        item_summary=item_summary
    )

@router.get("/job/{job_id}/items", summary="수집 작업 항목 목록 조회",
    description=(
        "수집 작업의 처리 항목 목록을 페이징 처리하여 조회합니다.  \n"
        "실패 항목의 원인은 `error_message`에서 확인할 수 있습니다."
    ),
    responses={
        200: {"description": "수집 작업 항목 목록을 반환합니다.", "model": BaseListResponse[IngestionJobItemInfo]},
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 수집 작업",
                            "value": {
                                "message": "수집 작업을 찾을 수 없습니다.",
                                "target": "job_id={job_id}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def get_ingestion_job_items(
    job_id: UUID = Path(
        ...,
        description="조회할 수집 작업 ID입니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    ),
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
    status: Optional[IngestionItemStatus] = Query(
        None,
        description="처리 상태로 필터링합니다.",
        examples=list(IngestionItemStatus)
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 수집 작업 조회
    query = (db_models.IngestionJob.select()
             .where(db_models.IngestionJob.id == job_id))
    job: Optional[db_items.IngestionJob] = await db_manager.select_item(query)
    if not job:
        return NotFoundResponse(
            message="수집 작업을 찾을 수 없습니다.",
            target=f"job_id={job_id}"
        )

    # 기본 쿼리
    query = (db_models.IngestionJobItem.select()
             .where(db_models.IngestionJobItem.job_id == job_id))
    count_query = (db_models.IngestionJobItem
                   .select(fn.COUNT(db_models.IngestionJobItem.id).alias("count"))
                   .where(db_models.IngestionJobItem.job_id == job_id))

    # 필터 조건
    if status:
        query = query.where(db_models.IngestionJobItem.status == status.value)
        count_query = count_query.where(db_models.IngestionJobItem.status == status.value)

    # 정렬 (생성 일시 오름차순)
    query = query.order_by(db_models.IngestionJobItem.created_at.asc())

    # 페이징
    if count > 0:
        offset = (page - 1) * count
        query = query.offset(offset).limit(count)

    # 총 개수 조회
    count_result = await db_manager.execute_query(count_query)
    total_count = count_result[0].count if count_result else 0

    # 목록 조회
    items: list[db_items.IngestionJobItem] = await db_manager.select_items(query)

    # 총 페이지 수
    if count > 0:
        total_pages = (total_count + count - 1) // count if total_count > 0 else 1
    else:
        total_pages = 1

    return BaseListResponse[IngestionJobItemInfo](
        total_pages=total_pages,
        total_count=total_count,
        items=[IngestionJobItemInfo(**item.model_dump()) for item in items]
    )

@router.delete("/job/{job_id}", summary="수집 작업 삭제",
    description=(
        "수집 작업 이력을 삭제합니다.  \n"
        "해당 작업의 처리 항목도 함께 삭제됩니다.  \n"
        "진행 중(`running`)인 작업은 삭제할 수 없습니다."
    ),
    responses={
        200: {"description": "수집 작업 삭제 결과를 반환합니다.", "model": BaseMessageResponse},
        404: {
            "description": "등록되지 않은 항목입니다.",
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 수집 작업",
                            "value": {
                                "message": "수집 작업을 찾을 수 없습니다.",
                                "target": "job_id={job_id}"
                            }
                        }
                    }
                }
            }
        },
        409: {
            "description": "삭제할 수 없는 상태입니다.",
            "model": ConflictError,
            "content": {
                "application/json": {
                    "examples": {
                        "running_job": {
                            "summary": "진행 중인 수집 작업",
                            "value": {
                                "message": "진행 중인 수집 작업은 삭제할 수 없습니다.",
                                "target": "job_id={job_id}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_FORBIDDEN,
    }
)
async def delete_ingestion_job(
    job_id: UUID = Path(
        ...,
        description="삭제할 수집 작업 ID입니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info_required_roles(config.auth.admin_roles)),
    logger: Logger = Depends(get_api_logger),
):
    # 수집 작업 조회
    query = (db_models.IngestionJob.select()
             .where(db_models.IngestionJob.id == job_id))
    job: Optional[db_items.IngestionJob] = await db_manager.select_item(query)
    if not job:
        return NotFoundResponse(
            message="수집 작업을 찾을 수 없습니다.",
            target=f"job_id={job_id}"
        )

    if job.status == IngestionStatus.RUNNING:
        return ConflictResponse(
            message="진행 중인 수집 작업은 삭제할 수 없습니다.",
            target=f"job_id={job_id}"
        )

    # 항목 삭제
    query = (db_models.IngestionJobItem.delete()
             .where(db_models.IngestionJobItem.job_id == job_id))
    await db_manager.execute_query(query, timeout=30.0)

    # 작업 삭제
    query = (db_models.IngestionJob.delete()
             .where(db_models.IngestionJob.id == job_id))
    await db_manager.execute_query(query)

    logger.debug(f"수집 작업이 삭제되었습니다. (job_id={job_id})")

    return BaseMessageResponse(
        message="수집 작업이 성공적으로 삭제되었습니다."
    )
