import os
import fcntl

from contextlib import asynccontextmanager

from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request as StarletteRequest

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import HTMLResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi

from app.config import env, fastapi_config, project_name
from app.api.v1 import api_v1_router
from app.scheduler.service import scheduler_service
from app.models.database import database_proxy
from app.utils.limiter import limiter
from app.utils.handlers import init_exception_handler, init_http_handler
from app.utils.database import get_database, try_initialize_database, close_db_resources
from app.utils.db_seed import seed_default_masking_and_guardrails
from app.utils.logger import get_logger
from app.utils.openapi import get_scalar_html
from app.security.runtime_guard import enforce_runtime_guard
import app.scheduler.jobs as scheduler_jobs


enforce_runtime_guard()

# 대용량 문서 file_data 폼 필드를 허용하기 위해 multipart 파트 크기 상한을 100MB로 확장
if StarletteRequest.form.__kwdefaults__ is None:
    StarletteRequest.form.__kwdefaults__ = {}
StarletteRequest.form.__kwdefaults__["max_part_size"] = 100 * 1024 * 1024  # 100MB

logger = get_logger("main", log_dir="logs")

# 메인 프로세스 Lock 파일 및 소유 플래그
MAIN_PROCESS_LOCK_FILE = f"/tmp/{project_name}_main_process.lock"
_is_main_process = False


# 서버 실행 시 동작 함수
@asynccontextmanager
async def api_lifespan(app: FastAPI):
    """FastAPI 애플리케이션의 수명 주기를 관리합니다.

    Args:
        app (FastAPI): FastAPI 애플리케이션 인스턴스
    """

    # 서버 실행 전
    logger.info(f"<{env.APP_ENV.upper()}> 서버가 실행되었습니다.")
    
    # DatabaseProxy 초기화
    try:
        db = get_database()
        if db is None:
            raise Exception("Database 연결에 실패하였습니다.")
        # DatabaseProxy에 실제 DB 바인딩
        database_proxy.initialize(db)
    except Exception:
        logger.exception("Database 초기화 중 오류가 발생했습니다.")
    
    # 메인 프로세스 Lock 획득 (File Lock으로 하나의 프로세스에서만 실행 보장)
    global _is_main_process
    
    try:
        lock_fd = os.open(MAIN_PROCESS_LOCK_FILE, os.O_CREAT | os.O_RDWR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        
        # 메인 프로세스로 지정
        _is_main_process = True
        logger.info("메인 프로세스로 지정되었습니다.")
        
        # Database 준비 및 테이블 생성 (메인 프로세스 전용)
        try:
            if await try_initialize_database(create_tables=True):
                await seed_default_masking_and_guardrails()
            else:
                logger.warning("Database를 사용할 수 없어 테이블 초기화를 건너뜁니다.")
        except Exception:
            logger.exception("Database 테이블 초기화 중 오류가 발생했습니다.")
            
        # 스케줄러 서비스 시작
        # 색인이 밀린 FAQ 재색인 (KAI-REQ-014)
        scheduler_service.scheduler.add_job(
            scheduler_jobs.sync_stale_faq_job,
            job_id="sync_stale_faq",
            trigger="interval",
            run_immediately=True,
            minutes=10,
        )
        # 규정 코퍼스 커버리지 점검 + 미완료분 재색인 (KAI-REQ-014)
        # FAQ와 달리 규정에는 이 잡이 없어서, 2026-08-03 재색인이 죽은 뒤
        # 45/181만 적재된 상태가 8일간 방치됐습니다.
        #
        # 주기가 FAQ(10분)보다 긴 이유: 문서 1건 적재가 평균 5.8분(CPU 임베딩)이라
        # 한 번 돌면 오래 걸립니다. 진행 중이면 잡 자신이 건너뛰므로 겹치지는 않지만,
        # 커버리지 점검 쿼리를 10분마다 돌릴 이유도 없습니다.
        scheduler_service.scheduler.add_job(
            scheduler_jobs.sync_incomplete_regulation_job,
            job_id="sync_incomplete_regulation",
            trigger="interval",
            run_immediately=True,
            minutes=60,
        )
        # 미입력 세션 자동 종료 (KAI-REQ-039)
        # 기동 직후에는 실행하지 않는다. 배포 재시작마다 살아있는 세션이 한꺼번에 끊기기 때문이다.
        # ENABLE_SESSION_IDLE_TIMEOUT=false면 잡을 등록하지 않는다. (세션이 유휴로 종료되지 않음)
        if env.ENABLE_SESSION_IDLE_TIMEOUT:
            scheduler_service.scheduler.add_job(
                scheduler_jobs.close_idle_sessions_job,
                job_id="close_idle_sessions",
                trigger="interval",
                run_immediately=False,
                minutes=5,
            )
        else:
            logger.info("미입력 세션 자동 종료가 비활성화되었습니다. (ENABLE_SESSION_IDLE_TIMEOUT=false)")
        scheduler_service.start()
    except BlockingIOError:
        # 이미 다른 프로세스가 메인 프로세스로 동작 중
        _is_main_process = False
    except Exception:
        logger.exception("메인 프로세스 Lock 파일 설정 중 오류가 발생했습니다.")

    # 서버 실행 중
    yield
    
    # 서버 종료 후
    
    # 메인 프로세스만 정리 작업 수행
    if _is_main_process:
        # 스케줄러 서비스 종료
        try:
            scheduler_service.stop()
        except Exception:
            logger.exception("스케줄러 종료 중 오류가 발생했습니다.")
    
    # Database 연결 종료
    try:
        await close_db_resources()
    except Exception:
        logger.exception("Database 종료 중 오류가 발생했습니다.")
    
    logger.info("서버가 종료되었습니다.")


#region FastAPI 애플리케이션 설정

app = FastAPI(default_response_class=ORJSONResponse, **fastapi_config, lifespan=api_lifespan)

# Rate Limiter 설정
app.state.limiter = limiter

# 미들웨어 설정
init_exception_handler(app)
init_http_handler(app)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=env.CORS_ORIGINS,
    allow_methods=env.CORS_METHODS,
    allow_headers=env.CORS_HEADERS,
    allow_credentials=True,
)

# # Response 압축 설정
if env.ENABLE_COMPRESS:
    from starlette_compress import CompressMiddleware
    app.add_middleware(CompressMiddleware)

#endregion

#region API Endpoints 설정

# 정적 파일 설정
app.mount("/static", StaticFiles(directory="static"), name="static")

# 버전별 API 라우터 포함
app.include_router(api_v1_router, prefix="/v1")

@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}

#endregion

#region 커스텀 OpenAPI 설정

def custom_openapi() -> dict:
    """OpenAPI 스키마를 커스텀하여 반환합니다.

    Returns:
        dict: OpenAPI 스키마
    """

    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes
    )
    
    new_paths = {}
    for path, path_data in openapi_schema["paths"].items():
        adjusted_path = f"{app.root_path}{path}" if app.root_path else path
        new_paths[adjusted_path] = path_data
    openapi_schema["paths"] = new_paths
    
    openapi_schema["info"]["x-logo"] = {
        "url": f"{app.root_path}/static/logo.png"
    }
    
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "description": "발급받은 API Key Token을 입력하세요."
        }
    }
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

@app.get("/docs", include_in_schema=False)  # Default API Docs
@app.get("/scalar", include_in_schema=False)
async def scalar_html(req: Request) -> HTMLResponse:
    if not env.ENABLE_SCALAR:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    root_path = req.scope.get("root_path", "").rstrip("/")
    openapi_url = root_path + (app.openapi_url or "")
    return get_scalar_html(
        openapi_url=openapi_url,
        title=f"{app.title} - API 명세서",
        scalar_js_url=f"{app.root_path}/static/js/scalar.js",
        scalar_favicon_url=f"{app.root_path}/static/favicon.png",
        theme="deepSpace"
    )

@app.get("/swagger", include_in_schema=False)
async def swagger_ui_html(req: Request) -> HTMLResponse:
    if not env.ENABLE_SWAGGER:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    root_path = req.scope.get("root_path", "").rstrip("/")
    openapi_url = root_path + (app.openapi_url or "")
    oauth2_redirect_url = app.swagger_ui_oauth2_redirect_url
    if oauth2_redirect_url:
        oauth2_redirect_url = root_path + oauth2_redirect_url
    return get_swagger_ui_html(
        openapi_url=openapi_url,
        title=f"{app.title} - API 명세서",
        oauth2_redirect_url=oauth2_redirect_url,
        init_oauth=app.swagger_ui_init_oauth,
        swagger_favicon_url=f"{app.root_path}/static/favicon.png",
        swagger_ui_parameters=app.swagger_ui_parameters
    )

@app.get("/redoc", include_in_schema=False)
async def redoc_html(req: Request) -> HTMLResponse:
    if not env.ENABLE_REDOC:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    root_path = req.scope.get("root_path", "").rstrip("/")
    openapi_url = root_path + (app.openapi_url or "")
    return get_redoc_html(
        openapi_url=openapi_url,
        title=f"{app.title} - API 명세서",
        redoc_favicon_url=f"{app.root_path}/static/favicon.png"
    )

#endregion
