"""학칙·규정 HWP 코퍼스를 벡터 저장소에 적재합니다. (KAI-REQ-014 학칙/규정 지식베이스)

`resources/regulations/*.hwp` 181개 문서가 원천이며, 적재 경로는 다음과 같습니다.

```
HWP 파일
  -> hwp_extractor.extract_text_async  (본문, hwp5txt)
  -> hwp_extractor.extract_tables_async(표,   hwp5html)
  -> regulation_chunker.chunk_document (문서메타 1 + 조항 N + 표 M 청크)
  -> faq_service.embed_texts           (64건씩 배치 임베딩)
  -> vector_store.upsert_points_async  (document_chunks_{dim} 테이블)
```

**왜 `documents` 테이블에 문서 행을 만드는가**

청크만 벡터로 밀어 넣고 `documents` 행은 만들지 않는 선택지도 있었지만, 다음 이유로
문서 1건 = `documents` 1행으로 두었습니다.

1. `document_chunks_*.document_id`는 NULL을 허용하지 않는 정수 컬럼이고,
   벡터 저장소가 제공하는 삭제/조회 API(`delete_points_by_document_id_async`,
   `get_points_by_document_id_async`)가 전부 이 값을 키로 씁니다.
   임의의 가짜 정수를 쓰면 재색인 시 "이 파일의 옛 청크"를 식별할 수 없어
   같은 문서의 청크가 계속 쌓입니다.
2. 관리자 API(`/document/list`, `/collection/*`)가 `documents` 기준으로 동작하므로,
   행을 만들어 두면 181개 규정의 적재 상태·청크 수·실패 사유를 기존 화면에서 그대로 봅니다.
3. `documents.metadata`에 원문 해시를 남겨 두면 변경 없는 문서를 건너뛸 수 있습니다.
   (FAQ 재색인의 `embedding_text_hash`와 같은 역할)

`documents.file_path`에는 S3 오브젝트 키가 아니라 리포지토리 상대 경로를 기록합니다.
규정 코퍼스는 사용자가 업로드한 파일이 아니라 리포지토리에 동봉된 원천이라, S3에 올리면
같은 파일이 두 곳에 존재하게 되고 어느 쪽이 정본인지 불분명해지기 때문입니다.

검색은 `VectorStoreManager.hybrid_search_async`(dense 0.55 + 어휘 0.45, top-k 12)를 씁니다.
청크 메타데이터(`doc_id`/`article`/`section_type`/`effective_date`/`table_id`/`dates_in_chunk`)를
payload에 그대로 실어 두는 이유가 이것입니다. 문서번호·조항 정확 일치 boost와 필터가
이 값들을 읽습니다.
"""

import os
import time
import hashlib
import unicodedata

from logging import Logger

from pathlib import Path

from typing import Any, Awaitable, Callable, Optional

from uuid import UUID, uuid5, NAMESPACE_URL

from pydantic import BaseModel, Field

from app.models.auth import TokenUserInfo
from app.models.enum import DocumentStatus, ModelType, ModelStatus
from app.utils.database import DatabaseManager
from app.utils.hwp_extractor import extract_text_async, extract_tables_async
from app.utils.query_synonyms import expand_for_embedding
from app.utils.logger import get_logger
from app.utils.regulation_chunker import chunk_document, parse_document_number
from app.utils.vector_store import get_vector_store_manager
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.common as util


try:
    logger = get_logger("regulation_ingest", log_dir="logs")
except OSError:
    # 로그 디렉터리에 쓸 수 없는 환경(테스트·샌드박스)에서 임포트가 통째로 실패하지 않도록
    # 콘솔 전용 Logger로 물러섭니다. (`hwp_extractor`와 같은 처리)
    logger = get_logger("regulation_ingest")


def _embed_texts():
    """`faq_service.embed_texts`를 지연 임포트해 반환합니다.

    임베딩 호출 자체는 FAQ 경로와 **완전히 같은 함수**를 씁니다.
    provider별 litellm 파라미터 구성과 차원 검증이 두 벌로 갈라지면 안 되기 때문입니다.

    모듈 상단이 아니라 호출 시점에 임포트하는 이유는, `faq_service`가 임포트 시점에
    파일 로거를 만들어(`get_logger(..., log_dir="logs")`) 로그 디렉터리에 쓸 수 없는
    환경에서는 임포트만으로 실패하기 때문입니다. 그러면 DB도 임베딩 모델도 필요 없는
    청킹·포인트 변환 테스트까지 함께 죽습니다. (`hwp_extractor`가 같은 이유로 로거를 감싸고 있습니다.)

    Returns:
        Callable: `faq_service.embed_texts` 함수
    """

    from app.utils.faq_service import embed_texts

    return embed_texts


REGULATION_COLLECTION_NAME = "kmu_regulations"
"""학칙·규정 지식베이스 논리 컬렉션명

FAQ(`kmu_faq_knowledge`)와 분리합니다. 리포트 8절이 "1-0-x 전용 인덱스(전체 181문서와 분리)"를
권고한 것과 같은 이유로, 성격이 다른 원천을 한 컬렉션에 섞으면 검색 노이즈가 됩니다.
FAQ는 `faq_embeddings` 전용 테이블을 쓰고, 규정은 범용 `document_chunks_{dim}` 테이블을 씁니다.
"""

REGULATION_SOURCE_TABLE = "documents"
"""규정 재색인 시 `ingestion_job_items.source_table`에 기록하는 원천 테이블명"""

REGULATION_DIR = Path(__file__).resolve().parents[2] / "resources" / "regulations"
"""규정 코퍼스 디렉터리 (`{repo}/resources/regulations`)"""

REGULATION_FILE_SUFFIX = ".hwp"
"""적재 대상 확장자"""

EMBED_BATCH_SIZE = 64
"""임베딩 배치 크기

181개 문서는 5,359개 청크가 나옵니다. 이를 한 번에 임베딩 API로 보내면 요청 본문이 수 MB가 되어
게이트웨이 타임아웃·모델 서버 OOM으로 통째로 실패합니다. 문서 하나 안에서도 배치로 끊습니다.
"""

INGEST_VERSION = "v1"
"""적재 스키마 버전

청킹 규칙이나 payload 구성을 바꾸면 이 값을 올립니다. 원문 해시 계산에 함께 들어가므로,
버전을 올리면 원문이 그대로여도 전량 재색인 대상이 됩니다.
"""

POINT_ID_NAMESPACE = uuid5(NAMESPACE_URL, "kmu-ai/regulations")
"""청크 포인트 ID 생성용 UUID 네임스페이스

포인트 ID를 `uuid5(파일명 + 청크 순번)`으로 고정하면 재색인 시 같은 청크가 같은 ID로 덮어써집니다.
(`upsert_points_async`가 ID 충돌 시 갱신) 삭제가 일부 실패해도 중복이 무한히 쌓이지 않는 안전장치입니다.
"""

MAX_CHUNK_CHARS = 1200
"""조항 청크 최대 길이. `regulation_chunker.chunk_document` 기본값과 맞춥니다."""


class RegulationIngestResult(BaseModel):
    """규정 문서 1건의 적재 결과"""

    file_name: str = Field(..., description="원본 파일명(NFC)입니다.")
    source_id: UUID = Field(..., description="수집 작업 항목에 기록할 원천 ID입니다.")
    document_id: Optional[int] = Field(None, description="`documents.id`입니다.")
    chunk_count: int = Field(0, description="적재한 청크 수입니다.")
    skipped: bool = Field(False, description="원문 변경이 없어 건너뛰었는지 여부입니다.")
    error_message: Optional[str] = Field(None, description="실패 사유입니다. 성공하면 None입니다.")

    @property
    def failed(self) -> bool:
        """실패 여부입니다."""

        return self.error_message is not None


def normalize_file_name(file_name: str) -> str:
    """파일명을 NFC로 정규화합니다.

    코퍼스 파일명이 디스크에 NFD(자모 분리)로 저장돼 있어, 정규화하지 않으면
    DB에 저장된 파일명과 코드에서 만든 문자열이 눈으로는 같은데 비교에서 어긋납니다.

    Args:
        file_name (str): 원본 파일명

    Returns:
        str: NFC 정규화된 파일명
    """

    return unicodedata.normalize("NFC", file_name)


def list_regulation_files(directory: Optional[str | Path] = None) -> list[Path]:
    """규정 코퍼스의 HWP 파일 경로를 정렬해 반환합니다.

    반환 경로는 **디스크에 있는 그대로**(NFD일 수 있음)입니다. 파일 접근은 이 경로로 하고,
    DB·메타데이터에 남길 이름은 `normalize_file_name()`으로 NFC 변환해 씁니다.

    Args:
        directory (Optional[str | Path]): 코퍼스 디렉터리. None이면 `REGULATION_DIR`

    Returns:
        list[Path]: HWP 파일 경로 목록 (NFC 파일명 기준 정렬)
    """

    base = Path(directory) if directory else REGULATION_DIR
    if not base.is_dir():
        logger.error(f"규정 코퍼스 디렉터리를 찾을 수 없습니다: {base}")
        return []

    files = [
        entry for entry in base.iterdir()
        if entry.is_file() and entry.suffix.lower() == REGULATION_FILE_SUFFIX
    ]
    return sorted(files, key=lambda path: normalize_file_name(path.name))


def make_source_id(file_name: str) -> UUID:
    """파일명으로 수집 작업 항목의 원천 ID를 만듭니다.

    `ingestion_job_items.source_id`는 UUID 컬럼인데, 규정의 원천은 DB 행이 아니라 파일이라
    가져다 쓸 UUID PK가 없습니다. `documents.id`는 정수라 그대로 넣을 수 없고,
    실행할 때마다 새 UUID를 쓰면 작업 이력에서 "같은 파일의 지난번 결과"를 추적할 수 없습니다.

    그래서 파일명에서 결정론적으로 UUID를 만듭니다. 같은 파일은 언제 재색인해도 같은
    `source_id`를 가지므로, 반복 실패하는 문서를 이력으로 바로 찾아낼 수 있습니다.

    Args:
        file_name (str): 파일명

    Returns:
        UUID: 파일명 기반 결정론적 UUID
    """

    return uuid5(POINT_ID_NAMESPACE, normalize_file_name(file_name))


def make_point_id(file_name: str, chunk_index: int) -> UUID:
    """청크 포인트 ID를 결정론적으로 만듭니다.

    Args:
        file_name (str): 파일명
        chunk_index (int): 문서 내 청크 순번

    Returns:
        UUID: 포인트 ID
    """

    return uuid5(POINT_ID_NAMESPACE, f"{normalize_file_name(file_name)}#{chunk_index}")


def compute_source_hash(chunks: list[dict[str, Any]]) -> str:
    """청크 목록에서 원문 해시를 계산합니다.

    파일 바이트가 아니라 **실제로 색인되는 청크 텍스트**를 해싱합니다.
    파일 바이트 해시는 HWP 내부 타임스탬프 같은 무의미한 변경에도 달라져 불필요한
    전량 재색인을 부르고, 반대로 청킹 규칙만 바뀐 경우는 잡아내지 못합니다.
    `INGEST_VERSION`을 함께 넣어 청킹·payload 구성이 바뀌면 자동으로 값이 달라지게 합니다.

    Args:
        chunks (list[dict[str, Any]]): 청크 목록

    Returns:
        str: 64자리 16진수 해시
    """

    digest = hashlib.sha256()
    digest.update(INGEST_VERSION.encode("utf-8"))
    for chunk in chunks:
        digest.update(b"\x00")
        digest.update((chunk.get("text") or "").encode("utf-8"))

    return digest.hexdigest()


def build_regulation_points(
    chunks: list[dict[str, Any]],
    vectors: list[list[float]],
    document_id: int,
    file_name: str,
) -> list[dict[str, Any]]:
    """청크와 임베딩 벡터를 벡터 저장소 포인트로 변환합니다.

    DB에 접근하지 않는 순수 함수라 실제 임베딩 모델 없이도 결과를 검증할 수 있습니다.

    청킹 메타데이터(`doc_id`/`article`/`section_type`/`effective_date`/`dates_in_chunk`/`table_id`)를
    payload에 그대로 싣습니다. `document_id`/`chunk_index`/`content`/`file_name`은 전용 컬럼으로
    승격되고 나머지는 `metadata` JSONB에 들어가지만, 조회 시에는 다시 하나의 평평한 payload로
    합쳐져 나옵니다. hybrid 검색의 문서번호·조항 boost가 이 값을 읽습니다.

    Args:
        chunks (list[dict[str, Any]]): `chunk_document()` 결과
        vectors (list[list[float]]): 청크와 같은 순서의 임베딩 벡터
        document_id (int): `documents.id`
        file_name (str): 원본 파일명

    Returns:
        list[dict[str, Any]]: `upsert_points_async`에 넘길 포인트 목록

    Raises:
        ValueError: 청크 수와 벡터 수가 다른 경우
    """

    if len(chunks) != len(vectors):
        raise ValueError(
            f"청크 수와 임베딩 벡터 수가 다릅니다. (chunks={len(chunks)}, vectors={len(vectors)})"
        )

    normalized_name = normalize_file_name(file_name)

    points = []
    for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
        points.append({
            "id": make_point_id(normalized_name, index),
            "vector": vector,
            "payload": {
                "document_id": document_id,
                "chunk_index": index,
                "content": chunk.get("text") or "",
                "file_name": normalized_name,
                # HWP 추출에는 페이지 개념이 없습니다. (조항/별표가 논리 단위)
                "page": None,
                "source": chunk.get("source"),
                "doc_id": chunk.get("doc_id"),
                "article": chunk.get("article"),
                "section_type": chunk.get("section_type"),
                "effective_date": chunk.get("effective_date"),
                "dates_in_chunk": chunk.get("dates_in_chunk") or [],
                "table_id": chunk.get("table_id"),
            },
        })

    return points


async def prepare_regulation_chunks(hwp_path: str | Path) -> list[dict[str, Any]]:
    """HWP 1건을 추출해 청크 목록을 만듭니다. (임베딩·DB 없음)

    본문은 `hwp5txt`, 표는 `hwp5html`로 각각 추출합니다. 본문 추출만으로는 표가
    `<표>` placeholder로만 남아 셀 수치 질의가 구조적으로 불가능하기 때문입니다(리포트 6.2절).

    표 추출은 실패해도 빈 리스트를 돌려주므로, 표가 깨진 문서도 본문 청크는 적재됩니다.

    Args:
        hwp_path (str | Path): HWP 파일 경로

    Returns:
        list[dict[str, Any]]: 청크 목록. 본문 추출에 실패하면 빈 리스트
    """

    path = Path(hwp_path)
    file_name = normalize_file_name(path.name)

    text = await extract_text_async(path)
    if not text or not text.strip():
        logger.error(f"본문을 추출하지 못했습니다: {file_name}")
        return []

    tables = await extract_tables_async(path, doc_id=parse_document_number(file_name))

    return chunk_document(text, tables, file_name, max_chars=MAX_CHUNK_CHARS)


async def embed_chunk_texts(
    model: db_items.Model,
    user_info: TokenUserInfo,
    texts: list[str],
    batch_size: int = EMBED_BATCH_SIZE,
) -> list[list[float]]:
    """청크 텍스트를 배치로 나눠 임베딩합니다.

    임베딩 호출 자체는 `faq_service.embed_texts`를 그대로 재사용합니다.
    (모델 provider별 litellm 파라미터 구성과 차원 검증이 이미 한 곳에 있어야 하기 때문)

    Args:
        model (db_items.Model): 임베딩 모델 정보
        user_info (TokenUserInfo): 사용자 정보
        texts (list[str]): 임베딩할 텍스트 목록
        batch_size (int): 한 번에 보낼 텍스트 수 (Default: 64)

    Returns:
        list[list[float]]: 입력과 같은 순서의 임베딩 벡터 목록
    """

    embed_texts = _embed_texts()

    vectors: list[list[float]] = []
    for offset in range(0, len(texts), max(batch_size, 1)):
        batch = texts[offset:offset + max(batch_size, 1)]
        vectors.extend(await embed_texts(model, user_info, batch))

    return vectors


async def get_regulation_collection(db_manager: DatabaseManager) -> Optional[db_items.Collection]:
    """학칙·규정 지식베이스 레지스트리 정보를 조회합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저

    Returns:
        Optional[db_items.Collection]: 컬렉션 정보 또는 None
    """

    query = (db_models.Collection.select()
             .where(db_models.Collection.name == REGULATION_COLLECTION_NAME))
    return await db_manager.select_item(query)


async def regulation_coverage(
    db_manager: DatabaseManager,
    directory: Optional[str | Path] = None,
) -> dict[str, Any]:
    """디스크의 HWP 파일과 실제 색인 결과를 대조합니다.

    **이 함수가 없어서 45/181만 적재된 상태를 8일 동안 아무도 몰랐습니다.**

    인제스트는 복구 장치를 갖추고 있습니다. `reap_stale_jobs()`가 죽은 잡을 회수하고,
    미완료 문서는 재실행 때 자동으로 다시 처리됩니다. 없었던 것은 **탐지**입니다 —
    "코퍼스에 181개가 있는데 45개만 색인됐다"를 아무도 비교하지 않았습니다.
    잡 카운터는 마감 시점에만 쓰였고, 그 잡은 마감되지 못하고 죽었습니다.

    그래서 진행 기록이 아니라 **결과 상태**를 직접 셉니다. 잡이 어떻게 죽었든
    이 값은 언제나 진실입니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        directory (Optional[str | Path]): 코퍼스 디렉터리. None이면 `REGULATION_DIR`

    Returns:
        dict[str, Any]: `{files, completed, missing, incomplete, chunks, complete, missing_files}`
            - `files`: 디스크의 HWP 수
            - `completed`: `documents.status='completed'`인 규정 문서 수
            - `missing`: 디스크에 있으나 완료되지 않은 파일 수
            - `incomplete`: `completed`가 아닌 상태로 남은 문서 수 (processing/error)
            - `complete`: 커버리지가 온전한지 여부
    """

    files = list_regulation_files(directory)
    disk_names = {normalize_file_name(path.name) for path in files}

    # 컬럼을 일부만 select하면 안 됩니다. `select_items()`가 행을 pydantic 모델로
    # 검증하는데, 빠진 컬럼(created_at 등)이 None이 되어 ValidationError로 죽습니다.
    # 문서는 181행뿐이라 전체를 읽어도 부담이 없습니다.
    query = (db_models.Document.select()
             .where(db_models.Document.collection_name == REGULATION_COLLECTION_NAME))
    documents = await db_manager.select_items(query) or []

    completed = {
        normalize_file_name(doc.file_name)
        for doc in documents
        if doc.status == DocumentStatus.COMPLETED
    }
    incomplete = [
        normalize_file_name(doc.file_name)
        for doc in documents
        if doc.status != DocumentStatus.COMPLETED
    ]

    missing_files = sorted(disk_names - completed)

    chunk_count = 0
    try:
        info = await get_vector_store_manager().get_collection_info_async(
            REGULATION_COLLECTION_NAME
        )
        chunk_count = info.get("points_count", 0)
    except Exception:  # noqa: BLE001 — 청크 수는 참고값이라 실패해도 커버리지는 내야 합니다
        logger.debug("규정 청크 수를 세지 못했습니다.", exc_info=True)

    return {
        "files": len(disk_names),
        "completed": len(completed & disk_names),
        "missing": len(missing_files),
        "incomplete": len(incomplete),
        "chunks": chunk_count,
        "complete": not missing_files,
        # 목록은 앞쪽 20개만 — 로그·응답이 181줄로 불어나면 아무도 안 읽습니다.
        "missing_files": missing_files[:20],
    }


async def ensure_regulation_collection(
    db_manager: DatabaseManager,
    embedding_model_name: str,
    user_info: TokenUserInfo,
) -> tuple[Optional[db_items.Collection], Optional[str]]:
    """학칙·규정 지식베이스 레지스트리를 준비합니다. (없으면 생성)

    FAQ와 마찬가지로 "어떤 임베딩 모델·차원으로 색인 중인지"를 `collections` 한 곳에서만
    관리해야 색인과 검색이 어긋나지 않습니다.

    실제 임베딩을 1건 호출해 차원을 확인한 뒤 레지스트리를 만듭니다. 차원이 어긋나면
    수천 개 청크를 다 임베딩한 뒤 pgvector INSERT 단계에서야 실패하기 때문입니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        embedding_model_name (str): 사용할 임베딩 모델명
        user_info (TokenUserInfo): 사용자 정보

    Returns:
        tuple[Optional[db_items.Collection], Optional[str]]: (컬렉션 정보, 오류 메시지)
    """

    collection = await get_regulation_collection(db_manager)
    if collection:
        return collection, None

    query = (db_models.Model.select()
             .where(db_models.Model.name == embedding_model_name)
             .where(db_models.Model.model_type == ModelType.EMBEDDING.value)
             .where(db_models.Model.status == ModelStatus.RUNNING.value))
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not model:
        return None, "등록되지 않은 임베딩 모델이거나 실행중이 아닙니다."

    try:
        await _embed_texts()(model, user_info, ["test"])
    except ValueError as exc:
        return None, str(exc)
    except Exception:
        logger.exception("임베딩 모델 테스트 중 오류가 발생했습니다.")
        return None, "임베딩 모델 테스트 중 오류가 발생했습니다."

    vector_size = db_models.FAQ_EMBEDDING_DIM
    vector_store = get_vector_store_manager()
    try:
        # 지원하지 않는 차원이면 여기서 걸러집니다. (차원별 정적 테이블만 존재)
        await vector_store.create_collection_async(
            collection_name=REGULATION_COLLECTION_NAME,
            vector_size=vector_size,
        )
    except ValueError as exc:
        return None, str(exc)

    query = db_models.Collection.insert(
        name=REGULATION_COLLECTION_NAME,
        user_name=user_info.username,
        embedding_model=embedding_model_name,
        vector_size=vector_size,
        description="계명대 학칙·규정 지식베이스 (hybrid 검색용)",
        is_system=True,
    )
    await db_manager.execute_query(query)

    logger.info(
        "학칙·규정 지식베이스 레지스트리가 생성되었습니다. "
        f"(vector_size={vector_size}, model={embedding_model_name})"
    )
    return await get_regulation_collection(db_manager), None


async def get_regulation_document(
    db_manager: DatabaseManager,
    file_name: str,
) -> Optional[db_items.Document]:
    """파일명으로 규정 문서 행을 조회합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        file_name (str): 파일명(NFC)

    Returns:
        Optional[db_items.Document]: 문서 정보 또는 None
    """

    query = (db_models.Document.select()
             .where(db_models.Document.collection_name == REGULATION_COLLECTION_NAME)
             .where(db_models.Document.file_name == normalize_file_name(file_name)))
    return await db_manager.select_item(query)


async def _create_regulation_document(
    db_manager: DatabaseManager,
    user_name: str,
    file_name: str,
    file_path: Path,
) -> int:
    """규정 문서 행을 새로 만듭니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        user_name (str): 등록자명
        file_name (str): 파일명(NFC)
        file_path (Path): 실제 파일 경로

    Returns:
        int: 생성된 `documents.id`
    """

    try:
        file_size = file_path.stat().st_size
    except OSError:
        file_size = 0

    # S3 키가 아니라 리포지토리 상대 경로를 기록합니다. (모듈 docstring 참고)
    try:
        relative_path = str(file_path.resolve().relative_to(REGULATION_DIR.parents[1]))
    except ValueError:
        relative_path = str(file_path)

    query = db_models.Document.insert(
        collection_name=REGULATION_COLLECTION_NAME,
        user_name=user_name,
        file_name=file_name,
        file_path=relative_path[:1024],
        file_size=file_size,
        file_type="hwp",
        status=DocumentStatus.PROCESSING,
    )
    return await db_manager.execute_query(query)


async def _update_regulation_document(
    db_manager: DatabaseManager,
    document_id: int,
    **fields: Any,
):
    """규정 문서 행을 갱신합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        document_id (int): `documents.id`
        **fields (Any): 갱신할 컬럼
    """

    query = (db_models.Document.update(updated_at=util.get_now(), **fields)
             .where(db_models.Document.id == document_id))
    await db_manager.execute_query(query)


async def ingest_regulation_file(
    db_manager: DatabaseManager,
    model: db_items.Model,
    user_info: TokenUserInfo,
    hwp_path: str | Path,
    force: bool = False,
    request_logger: Optional[Logger] = None,
) -> RegulationIngestResult:
    """규정 HWP 1건을 추출·청킹·임베딩해 벡터 저장소에 적재합니다.

    같은 파일을 다시 넣기 전에 해당 문서의 기존 포인트를 먼저 지웁니다.
    청킹 결과가 달라지면(조문 개정 등) 청크 수가 줄어들 수 있어, 덮어쓰기만으로는
    옛 청크가 남아 검색 결과에 유령 조문이 섞이기 때문입니다.

    문서 하나가 실패해도 예외를 밖으로 던지지 않고 `error_message`가 담긴 결과를 돌려줍니다.
    181개 문서 적재가 한 문서 때문에 멈추면 안 되기 때문입니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        model (db_items.Model): 임베딩 모델 정보
        user_info (TokenUserInfo): 사용자 정보
        hwp_path (str | Path): HWP 파일 경로
        force (bool): 원문 변경이 없어도 강제로 재색인할지 여부 (Default: False)
        request_logger (Optional[Logger]): 사용할 로거

    Returns:
        RegulationIngestResult: 적재 결과
    """

    log = request_logger or logger
    path = Path(hwp_path)
    file_name = normalize_file_name(path.name)
    source_id = make_source_id(file_name)

    document: Optional[db_items.Document] = None
    document_id: Optional[int] = None

    try:
        chunks = await prepare_regulation_chunks(path)
        if not chunks:
            return RegulationIngestResult(
                file_name=file_name,
                source_id=source_id,
                error_message="HWP 본문을 추출하지 못했거나 생성된 청크가 없습니다.",
            )

        source_hash = compute_source_hash(chunks)

        document = await get_regulation_document(db_manager, file_name)
        if document:
            document_id = document.id
            metadata = document.metadata or {}
            # 원문·청킹 규칙·임베딩 모델이 모두 그대로면 다시 임베딩할 이유가 없습니다.
            # (임베딩 호출이 이 경로에서 가장 비싼 단계입니다.)
            if (not force
                    and document.status == DocumentStatus.COMPLETED
                    and document.chunk_count > 0
                    and metadata.get("source_hash") == source_hash
                    and metadata.get("embedding_model") == model.name):
                return RegulationIngestResult(
                    file_name=file_name,
                    source_id=source_id,
                    document_id=document_id,
                    chunk_count=document.chunk_count,
                    skipped=True,
                )
            await _update_regulation_document(
                db_manager, document_id,
                status=DocumentStatus.PROCESSING,
                error_message=None,
            )
        else:
            document_id = await _create_regulation_document(
                db_manager,
                user_name=user_info.username,
                file_name=file_name,
                file_path=path,
            )

        vectors = await embed_chunk_texts(
            model=model,
            user_info=user_info,
            texts=[chunk.get("text") or "" for chunk in chunks],
        )
        points = build_regulation_points(
            chunks=chunks,
            vectors=vectors,
            document_id=document_id,
            file_name=file_name,
        )

        vector_store = get_vector_store_manager()
        # 재색인 시 옛 청크가 남지 않도록 먼저 비웁니다.
        await vector_store.delete_points_by_document_id_async(
            collection_name=REGULATION_COLLECTION_NAME,
            document_id=document_id,
        )
        await vector_store.upsert_points_async(REGULATION_COLLECTION_NAME, points)

        await _update_regulation_document(
            db_manager, document_id,
            chunk_count=len(points),
            metadata={
                "source_hash": source_hash,
                "ingest_version": INGEST_VERSION,
                "embedding_model": model.name,
                "doc_id": chunks[0].get("doc_id"),
                "effective_date": chunks[0].get("effective_date"),
                "table_chunks": sum(1 for chunk in chunks if chunk.get("section_type") == "table"),
            },
            status=DocumentStatus.COMPLETED,
            error_message=None,
        )

        log.debug(f"규정 문서를 적재했습니다. (file={file_name}, chunks={len(points)})")

        return RegulationIngestResult(
            file_name=file_name,
            source_id=source_id,
            document_id=document_id,
            chunk_count=len(points),
        )
    except Exception as exc:
        log.exception(f"규정 문서 적재 중 오류가 발생했습니다. (file={file_name})")

        if document_id is not None:
            try:
                await _update_regulation_document(
                    db_manager, document_id,
                    status=DocumentStatus.ERROR,
                    error_message=str(exc)[:1024],
                )
            except Exception:
                # 실패 상태 기록마저 실패해도 다음 문서 처리는 계속되어야 합니다.
                log.exception(f"문서 실패 상태를 기록하지 못했습니다. (file={file_name})")

        return RegulationIngestResult(
            file_name=file_name,
            source_id=source_id,
            document_id=document_id,
            error_message=str(exc)[:1024],
        )


async def ingest_regulation_directory(
    db_manager: DatabaseManager,
    model: db_items.Model,
    user_info: TokenUserInfo,
    directory: Optional[str | Path] = None,
    force: bool = False,
    on_result: Optional[Callable[[RegulationIngestResult], Awaitable[None]]] = None,
    request_logger: Optional[Logger] = None,
) -> list[RegulationIngestResult]:
    """규정 코퍼스 디렉터리 전체를 순회하며 적재합니다.

    문서 단위로 실패해도 중단하지 않습니다. `ingest_regulation_file`이 예외를 삼키고
    실패 결과를 돌려주므로, 손상된 HWP 한두 개가 나머지 180개 적재를 막지 못합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        model (db_items.Model): 임베딩 모델 정보
        user_info (TokenUserInfo): 사용자 정보
        directory (Optional[str | Path]): 코퍼스 디렉터리. None이면 `REGULATION_DIR`
        force (bool): 원문 변경이 없어도 강제로 재색인할지 여부 (Default: False)
        on_result (Optional[Callable[[RegulationIngestResult], Awaitable[None]]]):
            문서 1건이 끝날 때마다 호출할 콜백. 수집 작업 항목을 진행 중에 기록하는 데 씁니다.
        request_logger (Optional[Logger]): 사용할 로거

    Returns:
        list[RegulationIngestResult]: 문서별 적재 결과 목록
    """

    log = request_logger or logger
    files = list_regulation_files(directory)

    log.info(f"규정 코퍼스 적재를 시작합니다. (files={len(files)}, force={force})")

    results: list[RegulationIngestResult] = []
    for path in files:
        result = await ingest_regulation_file(
            db_manager=db_manager,
            model=model,
            user_info=user_info,
            hwp_path=path,
            force=force,
            request_logger=log,
        )
        results.append(result)

        if on_result:
            try:
                await on_result(result)
            except Exception:
                # 진행 상황 기록 실패가 적재 자체를 멈추게 해서는 안 됩니다.
                log.exception(f"적재 진행 상황을 기록하지 못했습니다. (file={result.file_name})")

    succeeded = sum(1 for result in results if not result.failed and not result.skipped)
    skipped = sum(1 for result in results if result.skipped)
    failed = sum(1 for result in results if result.failed)
    chunk_total = sum(result.chunk_count for result in results)

    log.info(
        "규정 코퍼스 적재가 완료되었습니다. "
        f"(total={len(results)}, success={succeeded}, skipped={skipped}, "
        f"failed={failed}, chunks={chunk_total})"
    )
    return results


# ===== 검색 (챗봇 응답 근거) =====

class RegulationSearchResult(BaseModel):
    """학칙·규정 hybrid 검색 결과"""

    document_id: int = Field(..., description="문서 ID입니다.")
    file_name: str = Field(..., description="원본 파일명입니다.")
    doc_id: Optional[str] = Field(None, description="규정 문서번호입니다. (예: 3-1-10)")
    article: Optional[str] = Field(None, description="조항 라벨입니다. (예: 제15조)")
    section_type: Optional[str] = Field(None, description="구간 유형입니다. (article/addendum/table 등)")
    effective_date: Optional[str] = Field(None, description="파일명 기준 시행일입니다.")
    content: str = Field(..., description="청크 원문입니다.")
    score: float = Field(..., description="hybrid 융합 점수입니다.")
    dense_score: Optional[float] = Field(None, description="dense(벡터) 점수입니다.")
    lexical_score: Optional[float] = Field(None, description="어휘(전문검색) 점수입니다.")


async def search_regulations(
    db_manager: DatabaseManager,
    user_info: TokenUserInfo,
    query_text: str,
    top_k: Optional[int] = None,
) -> tuple[list[RegulationSearchResult], int]:
    """학칙·규정 지식베이스에서 hybrid 검색으로 근거를 찾습니다. (KAI-REQ-013/015)

    FAQ는 "질문-답변" 쌍이라 dense 유사도만으로 충분하지만, 규정은 조문 번호·시행일 같은
    정확한 문자열이 질의에 그대로 등장하는 경우가 많아 어휘 검색을 섞어야 합니다.
    rag-test 실측(`rag-test/docs/rag_test_report.md` 5.1절)에서도 dense 단독 83.3% 대비
    hybrid + top-k=12가 91.7%로, 이 조합이 마지막 8.4%p를 만들었습니다.

    벡터 저장소·임베딩 모델을 쓸 수 없으면 예외를 던집니다. 호출부(챗봇 그래프)는
    이를 잡아 FAQ 근거만으로 답변을 이어가야 합니다.

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        user_info (TokenUserInfo): 사용자 정보
        query_text (str): 사용자 질문
        top_k (Optional[int]): 반환할 결과 수 (None이면 hybrid 기본값 12)

    Returns:
        tuple[list[RegulationSearchResult], int]: (검색 결과, 검색 지연 시간 ms)

    Raises:
        ValueError: 규정 지식베이스 또는 임베딩 모델이 준비되지 않은 경우
    """

    started = time.time()

    collection = await get_regulation_collection(db_manager)
    if not collection:
        raise ValueError("학칙·규정 지식베이스가 준비되지 않았습니다.")

    query = (db_models.Model.select()
             .where(db_models.Model.name == collection.embedding_model)
             .where(db_models.Model.model_type == ModelType.EMBEDDING.value)
             .where(db_models.Model.status == ModelStatus.RUNNING.value))
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not model:
        raise ValueError("학칙·규정 지식베이스의 임베딩 모델을 사용할 수 없습니다.")

    embed_texts = _embed_texts()
    # dense 임베딩용 동의어 확장. 2026-08-13 ablation으로 켜기로 했습니다.
    #
    # 어휘(FTS) 확장과 dense 확장을 갈라서 잰 이유는, 어휘 쪽은 tsquery를 `||`로 합쳐
    # 결과가 원래 집합의 **상위집합**이라 손해가 구조적으로 불가능한 반면, 임베딩에
    # 단어를 덧붙이는 것은 질의 의미 자체를 바꾸는 개입이라 손해가 날 수 있기 때문입니다.
    #
    # 실측 결과는 예상과 반대였습니다 (`docs/kmu_ai_eval.md` §5):
    #   - 어휘 확장 단독: 4회 실행에서 **바뀐 문항 0건**. 정답 조항이 동의어를 담고 있지
    #     않아(예: `5-1-7/제19조의2`에 "생활관" 없음) 문서만 올라오고 조항에 닿지 못함
    #   - dense 확장: b=0, c=1 (SAE-005). 학칙 boost와 결합 시 b=0 유지하며 c=3
    #
    # 즉 이득은 전부 dense 쪽에서 나옵니다. `KMU_DENSE_SYNONYM_EXPANSION=0`으로 끌 수 있게
    # 남겨 둔 것은 사전을 크게 늘릴 때 회귀를 다시 재기 위해서입니다.
    embed_query = query_text
    if os.environ.get("KMU_DENSE_SYNONYM_EXPANSION", "1") == "1":
        embed_query = expand_for_embedding(query_text)

    vectors = await embed_texts(model, user_info, [embed_query])

    vector_store = get_vector_store_manager()
    kwargs: dict[str, Any] = {
        "collection_name": REGULATION_COLLECTION_NAME,
        "query_text": query_text,
        "query_vector": vectors[0],
    }
    if top_k is not None:
        kwargs["limit"] = top_k
    hits = await vector_store.hybrid_search_async(**kwargs)

    results = [
        RegulationSearchResult(
            document_id=(hit.get("payload") or {}).get("document_id"),
            file_name=(hit.get("payload") or {}).get("file_name") or "",
            doc_id=(hit.get("payload") or {}).get("doc_id"),
            article=(hit.get("payload") or {}).get("article"),
            section_type=(hit.get("payload") or {}).get("section_type"),
            effective_date=(hit.get("payload") or {}).get("effective_date"),
            content=(hit.get("payload") or {}).get("content") or "",
            score=hit.get("score") or 0.0,
            dense_score=(hit.get("payload") or {}).get("dense_score"),
            lexical_score=(hit.get("payload") or {}).get("lexical_score"),
        )
        for hit in hits
    ]

    return results, int((time.time() - started) * 1000)
