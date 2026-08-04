import psycopg2
import asyncio

from peewee import ModelSelect, ModelUpdate, ModelInsert, ModelDelete, InterfaceError, OperationalError
from peewee_async import PooledPostgresqlDatabase, Manager

from fastapi import HTTPException, status

from psycopg2 import sql
from psycopg2.errors import UniqueViolation

from typing import Any, Optional

from app.config import config
from app.utils.logger import get_logger


logger = get_logger("database", log_dir="logs")


DATABASE_UNAVAILABLE_MESSAGE = "데이터베이스 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해주세요."


class ReconnectingDatabase(PooledPostgresqlDatabase):
    """커넥션 오류 발생 시 풀을 재연결하고 1회 재시도하는 데이터베이스 클래스.

    aio_execute_sql을 오버라이드하므로 aio_execute, 직접 SQL 호출 등
    모든 비동기 쿼리 경로에 재연결 로직이 적용됩니다.
    """

    async def _reconnect_pool(self) -> None:
        try:
            await self.aio_close()
        except Exception:
            pass
        await self.aio_connect()
        logger.info("데이터베이스 풀이 재연결되었습니다.")

    async def aio_execute_sql(self, sql_str: str, params=None, fetch_results=None):
        try:
            return await super().aio_execute_sql(sql_str, params, fetch_results)
        except (InterfaceError, OperationalError, RuntimeError) as e:
            logger.warning(f"DB 커넥션 오류 발생, 재연결 후 재시도합니다. ({e})")
            await self._reconnect_pool()
            return await super().aio_execute_sql(sql_str, params, fetch_results)


class DatabaseManager(Manager):
    """확장된 Database Manager 클래스"""
    
    async def wait_for_result(self, task: Any, timeout=5.0):
        """주어진 작업의 결과를 기다립니다.

        Args:
            task (Any): 기다릴 작업
            timeout (float, optional): 타임아웃 시간 (초) (Default: 5.0)

        Returns:
            작업 결과
        """
        
        return await asyncio.wait_for(task, timeout=timeout)

    async def execute_query(self, query: ModelSelect | ModelUpdate | ModelInsert | ModelDelete, timeout=5.0):
        """데이터베이스 쿼리를 실행하고 결과를 반환합니다.

        Args:
            query: 실행할 쿼리
            timeout (float, optional): 타임아웃 시간 (초) (Default: 5.0)

        Returns:
            쿼리 결과
        """

        await ensure_database_ready()
        try:
            db_task = self.execute(query)
            return await self.wait_for_result(db_task, timeout=timeout)
        except (InterfaceError, OperationalError, psycopg2.OperationalError, asyncio.TimeoutError) as exc:
            logger.exception("데이터베이스 쿼리 실행 중 오류가 발생했습니다.")
            # wait_for 타임아웃은 진행 중 쿼리를 cancel한다. 이때 커넥션이 풀로
            # 바로 안 돌아가면 이후 요청이 연쇄 503이 되므로 풀을 한 번 비운다.
            if isinstance(exc, asyncio.TimeoutError):
                try:
                    await self.database._reconnect_pool()
                except Exception:
                    logger.warning("타임아웃 후 DB 풀 재연결에 실패했습니다.", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=DATABASE_UNAVAILABLE_MESSAGE,
            ) from exc

    async def select_item(self, query: ModelSelect, timeout=5.0):
        """단일 아이템을 조회하고 Pydantic 모델로 변환합니다.
        
        Args:
            query: 실행할 쿼리
            timeout (float): 타임아웃 시간 (초) (Default: 5.0)

        Returns:
            변환된 Pydantic 모델 인스턴스 또는 None
        """
        
        import app.models.db_item as db_items
        
        result = await self.execute_query(query, timeout)
        for item in result:
            pydantic_class = getattr(db_items, item.__class__.__name__)
            return pydantic_class.from_db(item)
        return None
    
    async def select_items(self, query: ModelSelect, timeout=5.0):
        """여러 아이템을 조회하고 Pydantic 모델 리스트로 변환합니다.
        
        Args:
            query: 실행할 쿼리
            timeout (float): 타임아웃 시간 (초) (Default: 5.0)

        Returns:
            변환된 Pydantic 모델 인스턴스 리스트
        """
        
        import app.models.db_item as db_items
        
        result = await self.execute_query(query, timeout)
        items = []
        for item in result:
            pydantic_class = getattr(db_items, item.__class__.__name__)
            items.append(pydantic_class.from_db(item))
        return items


def create_database_if_not_exists(database_name: str):
    """데이터베이스가 존재하지 않는 경우 생성합니다.
    
    Args:
        database_name (str): 생성할 데이터베이스의 이름
    """
    
    try:
        # postgres 데이터베이스에 연결하여 새 데이터베이스 생성
        conn = psycopg2.connect(
            host=config.database.host,
            port=config.database.port,
            user=config.database.username,
            password=config.database.password,
            database="postgres"  # 기본 postgres 데이터베이스에 연결
        )
        conn.autocommit = True
        
        with conn.cursor() as cursor:
            # 데이터베이스 존재 여부 확인
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (database_name,)
            )
            
            if not cursor.fetchone():
                # 데이터베이스가 존재하지 않으면 생성
                try:
                    cursor.execute(
                        sql.SQL("CREATE DATABASE {}").format(
                            sql.Identifier(database_name)
                        )
                    )
                    logger.info(f"'{database_name}' 데이터베이스가 생성되었습니다.")
                except UniqueViolation:
                    pass
        
        conn.close()
    except psycopg2.OperationalError:
        logger.exception("데이터베이스 연결 중 오류가 발생했습니다.")
        raise
    except UniqueViolation:
        pass
    except psycopg2.Error:
        logger.exception("데이터베이스 생성 중 오류가 발생했습니다.")
        raise

def create_extensions_if_not_exists(database_name: str):
    """PostgreSQL 확장이 설치되지 않은 경우 설치합니다.
    
    Args:
        database_name (str): 확장을 설치할 데이터베이스의 이름
    """
    
    try:
        # 데이터베이스에 연결하여 확장 설치
        conn = psycopg2.connect(
            host=config.database.host,
            port=config.database.port,
            user=config.database.username,
            password=config.database.password,
            database=database_name
        )
        conn.autocommit = True
        
        with conn.cursor() as cursor:
            for extension in config.database.extensions:
                # 확장 설치 여부 확인
                cursor.execute(
                    "SELECT 1 FROM pg_extension WHERE extname = %s",
                    (extension,)
                )
                
                if not cursor.fetchone():
                    # 확장이 존재하지 않으면 설치
                    try:
                        cursor.execute(
                            sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(
                                sql.Identifier(extension)
                            )
                        )
                        logger.info(f"'{extension}' 확장이 설치되었습니다.")
                    except UniqueViolation:
                        pass
            
        conn.close()
    except psycopg2.OperationalError:
        logger.exception("데이터베이스 연결 중 오류가 발생했습니다.")
        raise
    except UniqueViolation:
        pass
    except psycopg2.Error:
        logger.exception("데이터베이스 확장 설치 중 오류가 발생했습니다.")
        raise


_db_instance: Optional[ReconnectingDatabase] = None
_db_manager_instance: Optional[DatabaseManager] = None
_db_ready = False


HNSW_MAX_DIMENSIONS = 2000
"""pgvector HNSW 인덱스가 지원하는 최대 차원.

이 값을 넘는 컬럼(예: OpenAI text-embedding-3-large의 3072차원)에는 인덱스를 만들 수 없어
검색이 순차 스캔으로 동작합니다. 저장·검색 자체는 되지만 데이터가 쌓이면 느려집니다.
"""


def _create_index_safely(db, statement: str, description: str):
    """인덱스 생성을 개별적으로 시도합니다.

    인덱스 생성을 한 덩어리로 묶어 실행하면 하나가 실패했을 때 뒤따르는 인덱스가 전부
    만들어지지 않습니다. (실제로 3072차원 HNSW 실패가 같은 테이블의 GIN 인덱스까지 막았습니다.)
    인덱스가 없어도 서비스는 동작하므로, 실패는 경고만 남기고 나머지를 계속 진행합니다.

    Args:
        db: 데이터베이스 인스턴스
        statement (str): 실행할 CREATE INDEX 문
        description (str): 로그에 남길 인덱스 설명
    """

    try:
        db.execute_sql(statement)
    except Exception as exc:
        logger.warning(f"{description} 생성에 실패했습니다. 검색은 동작하지만 느려질 수 있습니다. ({exc})")

def _create_hnsw_index(db, table_name: str, dimensions: int):
    """벡터 컬럼에 코사인 거리 기준 HNSW 인덱스를 만듭니다.

    Args:
        db: 데이터베이스 인스턴스
        table_name (str): 대상 테이블명 (코드 상수에서만 오므로 외부 입력이 섞이지 않습니다.)
        dimensions (int): 벡터 차원
    """

    if dimensions > HNSW_MAX_DIMENSIONS:
        logger.warning(
            f"{table_name}은 {dimensions}차원이라 HNSW 인덱스를 만들 수 없습니다. "
            f"(pgvector 상한 {HNSW_MAX_DIMENSIONS}차원) 벡터 검색이 순차 스캔으로 동작합니다."
        )
        return

    _create_index_safely(
        db,
        f"CREATE INDEX IF NOT EXISTS {table_name}_embedding_hnsw_idx "
        f"ON {table_name} USING hnsw (embedding vector_cosine_ops)",
        f"{table_name} 벡터 검색(HNSW) 인덱스",
    )


def _try_initialize_database_sync(create_tables: bool = False) -> bool:
    """데이터베이스 준비 작업을 동기적으로 수행합니다.

    Args:
        create_tables (bool, optional): 테이블 생성 여부 (Default: False)

    Returns:
        bool: 데이터베이스 준비 여부
    """

    global _db_ready
    if _db_ready:
        return True

    try:
        if config.database.db_type == "postgresql":
            create_database_if_not_exists(config.database.name)
            create_extensions_if_not_exists(config.database.name)

        db = get_database()
        if create_tables:
            import app.models.database as db_models
            db.create_tables(db_models.TABLES, safe=True)
            # faq_embeddings.embedding 벡터 유사도 검색용 인덱스 (pgvector). 코사인 거리 기준 HNSW.
            # 인덱스는 컬럼 차원에 고정되므로 chatbot.embedding_dim을 바꾸면 DROP 후 재생성해야 한다.
            _create_hnsw_index(db, "faq_embeddings", db_models.FAQ_EMBEDDING_DIM)
            # 문서 청크(범용 RAG) 벡터 유사도 검색용 인덱스. 차원별 테이블마다 하나씩 만든다.
            # 테이블명은 코드에 고정된 상수(DOCUMENT_CHUNK_MODELS)에서만 오므로 외부 입력이 섞이지 않는다.
            for dimensions, chunk_model in db_models.DOCUMENT_CHUNK_MODELS.items():
                table_name = chunk_model._meta.table_name
                _create_hnsw_index(db, table_name, dimensions)
                # hybrid 검색(dense + 어휘)의 어휘 측 인덱스.
                # 인덱스 표현식은 VectorStoreManager._lexical_expressions()가 만드는
                # to_tsvector('simple', content)와 문자 그대로 같아야 플래너가 사용한다.
                # 한국어 형태소 분석기가 없어 'simple'(공백 토큰화) 설정을 쓴다.
                _create_index_safely(
                    db,
                    f"CREATE INDEX IF NOT EXISTS {table_name}_content_tsv_gin_idx "
                    f"ON {table_name} USING gin (to_tsvector('simple', content))",
                    f"{table_name} 어휘 검색(GIN) 인덱스",
                )
    except (psycopg2.OperationalError, InterfaceError, OperationalError, OSError):
        logger.exception("데이터베이스 초기화 중 오류가 발생했습니다.")
        return False

    _db_ready = True
    return True

async def try_initialize_database(create_tables: bool = False) -> bool:
    """데이터베이스 준비 작업을 이벤트 루프를 막지 않고 시도합니다.

    Args:
        create_tables (bool, optional): 테이블 생성 여부 (Default: False)

    Returns:
        bool: 데이터베이스 준비 여부
    """

    if _db_ready:
        return True

    try:
        return await asyncio.to_thread(_try_initialize_database_sync, create_tables)
    except (psycopg2.OperationalError, InterfaceError, OperationalError, OSError):
        logger.exception("데이터베이스 초기화 중 오류가 발생했습니다.")
        return False

async def ensure_database_ready():
    """요청 처리 전 데이터베이스 사용 가능 여부를 확인합니다.

    Raises:
        HTTPException: 데이터베이스 사용 불가 시 예외 발생
    """

    if await try_initialize_database():
        return

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=DATABASE_UNAVAILABLE_MESSAGE,
    )

def get_database() -> Optional[ReconnectingDatabase]:
    """데이터베이스 연결을 관리합니다.

    Raises:
        ValueError: 지원하지 않는 데이터베이스 타입인 경우 예외 발생

    Returns:
        ReconnectingDatabase: PostgreSQL 데이터베이스 인스턴스
    """
    
    global _db_instance
    if _db_instance is not None:
        return _db_instance
    
    db = None
    
    if config.database.db_type == "postgresql":
        db = ReconnectingDatabase(
            config.database.name,
            user=config.database.username,
            password=config.database.password,
            host=config.database.host,
            port=config.database.port,
            min_connections=config.database.pool_min_connections,
            max_connections=config.database.pool_max_connections,
        )
    
    if db is None:
        raise ValueError("지원하지 않는 데이터베이스입니다.")
    
    _db_instance = db
    return _db_instance

async def get_db_manager() -> DatabaseManager:
    """Database Manager 인스턴스를 관리합니다.

    Returns:
        DatabaseManager: Database Manager 인스턴스
    """

    global _db_manager_instance
    if _db_manager_instance is None:
        _db_manager_instance = DatabaseManager(get_database())

    return _db_manager_instance

async def close_db_resources():
    """애플리케이션 종료 시 데이터베이스 리소스를 정리합니다."""
    global _db_instance, _db_manager_instance
    try:
        if _db_manager_instance is not None:
            await _db_manager_instance.close()
    finally:
        if _db_instance is not None:
            await _db_instance.aio_close()
        _db_instance = None
        _db_manager_instance = None
