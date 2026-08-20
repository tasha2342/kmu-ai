import os
import re
import uuid

from typing import Optional, Any

from peewee import fn, Expression, SQL

from app.utils.database import get_db_manager
from app.utils.logger import get_logger
from app.utils.query_synonyms import lexical_expansion_terms
import app.models.database as db_models
from app.utils import milvus_store as milvus_store_mod


try:
    logger = get_logger("vector_store", log_dir="logs")
except OSError:
    # 로그 디렉터리에 쓸 수 없는 환경(테스트·샌드박스)에서 임포트 자체가 실패하면
    # DB 없이 검증 가능한 융합 로직 테스트까지 같이 죽습니다.
    # 이때는 콘솔 전용 Logger로 물러섭니다. (`hwp_extractor`와 같은 처리)
    logger = get_logger("vector_store")


SUPPORTED_VECTOR_SIZES: tuple[int, ...] = tuple(sorted(db_models.DOCUMENT_CHUNK_MODELS))
"""지원하는 임베딩 벡터 차원 목록

pgvector의 HNSW 인덱스는 차원이 고정된 컬럼에만 걸 수 있어 차원별 정적 테이블을 씁니다.
목록에 없는 차원의 임베딩 모델은 컬렉션 생성 단계에서 거부됩니다.
"""

DEFAULT_DISTANCE = "Cosine"
"""거리 측정 방식

HNSW 인덱스를 `vector_cosine_ops`로 고정 생성하므로 코사인만 지원합니다.
다른 방식을 쓰려면 인덱스를 함께 바꿔야 하므로 런타임에 선택할 수 없습니다.
"""

PAYLOAD_COLUMN_KEYS = ("document_id", "chunk_index", "content", "page", "file_name")
"""payload에서 전용 컬럼으로 승격되는 키 목록 (나머지는 `metadata` JSONB에 저장)"""

UPSERT_BATCH_SIZE = 100
"""한 번의 INSERT에 담을 청크 수 (문장이 지나치게 길어져 타임아웃되는 것을 막습니다.)"""

BULK_QUERY_TIMEOUT = 60.0
"""대량 저장·삭제 쿼리의 타임아웃(초). 기본 5초로는 수백 개 청크 저장이 중간에 끊깁니다."""

SEARCH_QUERY_TIMEOUT = 30.0
"""벡터·hybrid 검색 쿼리 타임아웃(초).

기본 5초는 임베딩 CPU 부하·스왑이 겹칠 때 HNSW 검색이 넘기기 쉽고, 타임아웃이
풀 고갈로 번지면 같은 턴의 응답 생성(모델 조회)까지 503이 난다.
"""


# ===== hybrid 검색 상수 =====
#
# 아래 값들은 임의로 고른 수치가 아니라 `rag-test/docs/rag_test_report.md`의
# 계명대 규정 골드셋 48문항 측정 결과에서 온 값입니다(리포트 5.1절 / 8절).
#
# | 구성 | 전체 | Recall@K |
# |---|---|---|
# | E3 dense only, k=4 | 83.3% | 81.3% |
# | E4 hybrid(dense .55 + BM25 .45) + 문서/조항 boost, k=12 | 91.7% | 89.6% |
#
# 즉 가중치와 top-k는 함께 측정된 한 세트이므로, 근거 없이 따로 바꾸면 안 됩니다.

TEXT_SEARCH_CONFIG = "simple"
"""PostgreSQL 전문검색 설정 이름

한국어 형태소 분석기(`mecab`/`textsearch_ko` 등)가 설치돼 있지 않아 `simple`을 씁니다.
`simple`은 **공백·구두점 기준 토큰화 + 소문자화만** 수행하므로 다음 한계가 있습니다.

- 조사가 붙은 형태를 다른 토큰으로 봅니다. (`학칙은` != `학칙`)
- 어간 추출·불용어 제거가 없습니다.
- 복합명사를 분해하지 못합니다. (`휴학신청기간` != `휴학` + `신청` + `기간`)

그래서 어휘 검색은 **정확히 일치하는 표기**(문서번호 `3-1-10`, 조항 `제15조`, 표 라벨,
`YYYY-MM-DD` 날짜처럼 임베딩이 약한 리터럴)를 잡는 용도로만 신뢰하고,
의미 유사도는 dense 쪽에 맡깁니다. 두 검색기의 강점이 겹치지 않기 때문에
융합했을 때 Recall이 81.3% → 89.6%로 오른 것입니다.
"""

DENSE_WEIGHT = 0.55
"""dense(임베딩 코사인) 점수 가중치. 리포트 8절 최종 권장 구성값입니다."""

LEXICAL_WEIGHT = 0.45
"""어휘(전문검색) 점수 가중치. 리포트 8절 최종 권장 구성값입니다."""

DEFAULT_HYBRID_TOP_K = 12
"""hybrid 검색 기본 top-k

리포트 5.1절에서 k=4는 85.4%, k=8은 79.2%, **k=12는 91.7%**였습니다.
표 질의는 인접 표 청크가 함께 들어와야 정답 셀에 닿기 때문에 k가 작으면 오히려 손해입니다.
"""

CANDIDATE_MULTIPLIER = 4
"""후보군 배수. 각 검색기에서 `limit * 4`건씩 뽑아 융합합니다.

한쪽 검색기에서만 상위권인 청크가 융합 후 올라올 수 있어야 hybrid의 의미가 있습니다.
후보를 `limit`만큼만 뽑으면 dense 상위 12건과 어휘 상위 12건의 합집합에 그쳐,
"dense 20위 + 어휘 1위"처럼 융합으로 살아나야 할 청크를 처음부터 놓칩니다.
"""

DOC_ID_BOOST = 0.15
"""질의에 명시된 문서번호와 청크의 `doc_id`가 정확히 일치할 때의 가산점"""

ARTICLE_BOOST = 0.10
"""질의에 명시된 조항과 청크의 `article`이 정확히 일치할 때의 가산점"""

PRIMARY_DOC_ID = "2-0-1"
"""상위 규범 문서(학칙) 번호.

학칙은 학사 질문의 1차 근거인데 주변 규정 180건에 순위가 밀립니다. 2026-08-12 실측
(`docs/kmu_ai_eval.md` §1)에서 top-k=12로도 못 찾은 12건 중 **6건이 학칙**이었고,
"수업연한 몇 년"처럼 학칙 제5조에 그 단어가 그대로 있는 질문까지 놓쳤습니다.
"""

PRIMARY_DOC_BOOST = float(os.environ.get("KMU_PRIMARY_DOC_BOOST", "0.05") or 0)
"""학칙 조문에 주는 가산점. **0.05는 스윕으로 정한 값입니다.**

`DOC_ID_BOOST`(0.15)와 달리 이 가산점은 질의에 근거가 없는 **선험적 편향**입니다.
학칙을 올리면 다른 규정이 밀려나므로 평균이 아니라 McNemar의 `b`(깨진 문항)로
판정했습니다. 2026-08-13 실측(`docs/kmu_ai_eval.md` §6), 학생 골드셋 185문항:

| boost | recall_article@12 | 학칙(57) | 비학칙(128) | TRIG(15) | b | c |
| --- | --- | --- | --- | --- | --- | --- |
| 0    | 172/185 | 51 | 121 | 12 | — | — |
| 0.02 | 173/185 | 52 | 121 | 12 | 0 | 1 |
| 0.03 | 174/185 | 53 | 121 | 12 | 0 | 2 |
| **0.05** | **174/185** | **53** | **121** | **13** | **0** | **2** |
| 0.07 | 174/185 | 53 | 121 | 13 | 0 | 2 |
| 0.10 | 173/185 | 53 | **120** | 13 | **1** | 2 |
| 0.15 | 173/185 | 53 | **120** | 13 | **1** | 2 |

**상한 근거**: 0.10부터 비학칙이 121 → 120으로 깎입니다(HYU-016). 학칙 이득은
0.03에서 이미 포화(53/57)되므로 그 위로는 이득 없이 손해만 커집니다.
**하한 근거**: 0.02는 c=1로 이득이 절반입니다.
0.03/0.05/0.07이 동일한 고원이고, 그중 TRIG 대조군을 하나 더 살리는 최소값이 0.05입니다.

값을 바꾸려면 `KMU_PRIMARY_DOC_BOOST`로 덮어쓴 뒤 위 표를 다시 만드세요.
평균만 보고 올리면 안 됩니다 — 0.10의 평균은 기준선보다 높은데도 `b`가 생깁니다.
"""

PRIMARY_DOC_SECTION_TYPES = ("article",)
"""학칙 가산점을 줄 청크 유형. 조문만 올립니다.

학칙 255청크 중 `addendum`(부칙)이 80건입니다. 부칙은 개정 이력 날짜를 잔뜩 품고
있어 이미 과다 노출 경향이 있고(리포트 6.3절), 여기에 가산점까지 주면 정작 조문이
부칙에 밀립니다. `chapter`·`section`은 제목 줄이라 근거로 쓸 내용이 없습니다.
"""

# 질의에 박힌 문서번호(`3-1-10`)와 조항(`제15조`, `제15조의2`) 패턴.
# 청킹 단계(`regulation_chunker`)가 만드는 `doc_id`/`article` 표기와 형식을 맞춥니다.
#
# 각 자리 수를 제한한 이유: `\d+-\d+-\d+`로 두면 `2024-03-01` 같은 **날짜를 문서번호로 오인**합니다.
# 규정 질의에는 날짜가 매우 자주 등장하므로(시행일·개정일), 그대로 두면 존재하지도 않는
# 문서번호 신호가 계속 잡혀 boost가 무의미해집니다. 실제 코퍼스의 문서번호는
# `1-0-1` ~ `5-1-x` 형태로 앞 두 자리가 한 자리 수이고 마지막이 최대 두 자리입니다.
QUERY_DOC_ID_RE = re.compile(r"(?<![\d-])(\d{1,2}-\d{1,2}-\d{1,3})(?![\d-])")
QUERY_ARTICLE_RE = re.compile(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?")


def parse_query_signals(query_text: str) -> dict[str, list[str]]:
    """질의문에서 문서번호·조항 같은 정확 일치 신호를 뽑습니다.

    리포트 6.3절은 "조항 청킹만으로는 날짜 검색이 오히려 악화되는 경우가 있다
    (부칙 청크 과다 노출)"고 관찰했습니다. 부칙 청크는 개정 이력 날짜를 잔뜩 품고 있어
    날짜·조항 질의에서 임베딩 유사도가 높게 나오지만, 정작 사용자가 물은 조문은 아닙니다.

    이 함수가 뽑아낸 신호는 `compute_boost()`에서 **청크 메타데이터와의 정확 일치**에만
    가산점을 주는 데 쓰입니다. 본문에 `제15조`가 언급되기만 한 부칙 청크는
    `article`이 `부칙`이라 가산점을 받지 못하고, 실제 제15조 청크만 올라옵니다.

    Args:
        query_text (str): 사용자 질의문

    Returns:
        dict[str, list[str]]: `{"doc_ids": [...], "articles": [...]}`
    """

    text = query_text or ""

    doc_ids = []
    for match in QUERY_DOC_ID_RE.finditer(text):
        if match.group(1) not in doc_ids:
            doc_ids.append(match.group(1))

    articles = []
    for match in QUERY_ARTICLE_RE.finditer(text):
        number, sub = match.group(1), match.group(2)
        # 질의는 `제 15 조의 2`처럼 띄어 쓸 수 있으므로 공백을 없앤 정규 표기로 맞춥니다.
        article = f"제{number}조의{sub}" if sub else f"제{number}조"
        if article not in articles:
            articles.append(article)

    return {"doc_ids": doc_ids, "articles": articles}


def min_max_normalize(values: list[float]) -> list[float]:
    """점수 목록을 후보군 내 min-max로 0~1 구간에 정규화합니다.

    dense 점수는 코사인 유사도(대략 0.3~0.9)이고 어휘 점수는 `ts_rank_cd`
    (0에서 시작해 상한이 없는 값)라 스케일이 전혀 다릅니다. 정규화 없이 가중합하면
    가중치 0.55/0.45가 의도한 비율대로 동작하지 않고 한쪽이 사실상 무시됩니다.

    모든 값이 같으면(후보 1건이거나 동점) 1.0으로 채웁니다. 0.0으로 채우면
    그 검색기가 찾아낸 결과가 융합 점수에 전혀 기여하지 못해, 어휘 검색이 정확히
    맞춘 단 하나의 후보가 사라지는 부작용이 생깁니다.

    Args:
        values (list[float]): 원본 점수 목록

    Returns:
        list[float]: 0~1로 정규화된 점수 목록
    """

    if not values:
        return []

    lowest = min(values)
    highest = max(values)
    if highest - lowest < 1e-12:
        return [1.0] * len(values)

    return [(value - lowest) / (highest - lowest) for value in values]


def compute_boost(payload: dict[str, Any], signals: dict[str, list[str]]) -> float:
    """질의 신호와 청크 메타데이터의 정확 일치 가산점을 계산합니다.

    본문 포함 여부가 아니라 **메타데이터 정확 일치**만 봅니다.
    본문 언급까지 인정하면 개정 이력에 `제15조`가 나열된 부칙 청크가 전부 가산점을 받아
    리포트 6.3절이 지적한 "부칙 청크 과다 노출"을 오히려 악화시킵니다.

    Args:
        payload (dict[str, Any]): 청크 payload (`doc_id`/`article` 포함)
        signals (dict[str, list[str]]): `parse_query_signals()` 결과

    Returns:
        float: 가산점 합계 (0.0 ~ `DOC_ID_BOOST + ARTICLE_BOOST`)
    """

    boost = 0.0

    doc_id = payload.get("doc_id")
    if doc_id and doc_id in signals.get("doc_ids", []):
        boost += DOC_ID_BOOST

    article = payload.get("article")
    if article and article in signals.get("articles", []):
        boost += ARTICLE_BOOST

    # 상위 규범(학칙) 가산점. 질의에 문서번호가 **없을 때만** 줍니다.
    # 사용자가 "3-1-10 제5조"처럼 특정 문서를 짚었다면 그 의도가 우선이고,
    # 여기서 학칙을 올리면 명시적 요청을 뒤집게 됩니다.
    if (
        PRIMARY_DOC_BOOST
        and doc_id == PRIMARY_DOC_ID
        and not signals.get("doc_ids")
        and payload.get("section_type") in PRIMARY_DOC_SECTION_TYPES
    ):
        boost += PRIMARY_DOC_BOOST

    return boost


def fuse_hybrid_scores(
    dense_scores: dict[str, float],
    lexical_scores: dict[str, float],
    boosts: Optional[dict[str, float]] = None,
    dense_weight: float = DENSE_WEIGHT,
    lexical_weight: float = LEXICAL_WEIGHT,
    limit: int = DEFAULT_HYBRID_TOP_K,
) -> list[dict[str, Any]]:
    """dense 점수와 어휘 점수를 정규화 후 가중합해 재순위합니다.

    **융합 공식**

    ```
    dense_norm   = minmax(dense_scores,   후보군 전체 기준)
    lexical_norm = minmax(lexical_scores, 후보군 전체 기준)
    final = 0.55 * dense_norm + 0.45 * lexical_norm + boost
    ```

    **왜 RRF가 아니라 정규화 가중합인가**
    리포트 E4가 측정한 구성이 `dense 0.55 + BM25 0.45`의 점수 가중합입니다.
    RRF(순위 기반 융합)는 가중치를 순위로 흡수해 버려 이 비율을 재현할 수 없고,
    "dense가 압도적으로 확신하는 1건"과 "간신히 12위로 들어온 1건"의 차이도 사라집니다.
    측정된 구성을 그대로 옮기는 것이 목적이므로 점수 가중합을 씁니다.

    **한쪽에만 있는 후보**
    상대 검색기의 후보군에 없으면 그 쪽 정규화 점수는 0으로 둡니다.
    (= 그 검색기 기준으로는 최하위라는 뜻) 결과적으로 두 검색기가 모두 찾아낸 청크가
    자연스럽게 위로 올라오며, 이것이 hybrid가 Recall을 끌어올리는 핵심 동작입니다.

    **min-max의 성질 (알고 쓰세요)**
    각 후보군의 최하위는 정규화 결과가 정확히 0이 됩니다. 따라서 원점수 차이가 아무리
    작아도 후보군 안에서는 0~1 전 구간으로 벌어집니다. 후보를 `limit * CANDIDATE_MULTIPLIER`
    (기본 48)건씩 넉넉히 뽑는 이유가 이것입니다. 후보가 2~3건뿐이면 사소한 점수 차가
    순위를 과장하고, boost로도 뒤집기 어려워집니다.

    Args:
        dense_scores (dict[str, float]): 청크 ID -> dense 원점수 (코사인 유사도)
        lexical_scores (dict[str, float]): 청크 ID -> 어휘 원점수 (`ts_rank_cd`)
        boosts (Optional[dict[str, float]]): 청크 ID -> 가산점
        dense_weight (float): dense 가중치
        lexical_weight (float): 어휘 가중치
        limit (int): 반환할 상위 건수

    Returns:
        list[dict[str, Any]]: 융합 점수 내림차순 목록
            (`{"id", "score", "dense_score", "lexical_score", "dense_norm", "lexical_norm", "boost"}`)
    """

    boosts = boosts or {}

    dense_ids = list(dense_scores)
    lexical_ids = list(lexical_scores)

    dense_norm = dict(zip(dense_ids, min_max_normalize([dense_scores[i] for i in dense_ids])))
    lexical_norm = dict(zip(lexical_ids, min_max_normalize([lexical_scores[i] for i in lexical_ids])))

    # 두 후보군의 합집합. dense 결과를 먼저 두어 동점일 때 dense 순서가 유지되게 합니다.
    candidate_ids = dense_ids + [point_id for point_id in lexical_ids if point_id not in dense_norm]

    fused = []
    for point_id in candidate_ids:
        boost = boosts.get(point_id, 0.0)
        score = (dense_weight * dense_norm.get(point_id, 0.0)
                 + lexical_weight * lexical_norm.get(point_id, 0.0)
                 + boost)
        fused.append({
            "id": point_id,
            "score": score,
            "dense_score": dense_scores.get(point_id),
            "lexical_score": lexical_scores.get(point_id),
            "dense_norm": dense_norm.get(point_id, 0.0),
            "lexical_norm": lexical_norm.get(point_id, 0.0),
            "boost": boost,
        })

    fused.sort(key=lambda item: item["score"], reverse=True)
    return fused[:limit] if limit and limit > 0 else fused


class VectorStoreManager:
    """벡터 저장소 관리 클래스 (PostgreSQL pgvector + 선택적 Milvus)

    범용 문서 RAG의 벡터를 저장합니다. 컬렉션은 물리적으로 분리되지 않고,
    차원별 테이블(`document_chunks_{dim}`) / Milvus 컬렉션 안에서
    `collection_name` 컬럼으로 구분됩니다.

    `config.milvus.enabled=False`(기본): dense 읽기/쓰기는 pgvector만.
    `True`: dense 읽기=Milvus, 쓰기=Milvus+PG dual-write.
    hybrid의 어휘(FTS)는 항상 Postgres `content` GIN을 쓴다.

    거리 측정은 코사인으로 고정입니다.
    """

    def _get_model(self, vector_size: int) -> type[db_models.BaseDocumentChunk]:
        """벡터 차원에 대응하는 청크 테이블 모델을 반환합니다.

        Args:
            vector_size (int): 벡터 차원 크기

        Returns:
            type[db_models.BaseDocumentChunk]: 청크 테이블 모델

        Raises:
            ValueError: 지원하지 않는 차원인 경우
        """

        model = db_models.DOCUMENT_CHUNK_MODELS.get(vector_size)
        if model is None:
            raise ValueError(
                f"지원하지 않는 임베딩 차원입니다. (vector_size={vector_size}) "
                f"지원 차원: {', '.join(str(size) for size in SUPPORTED_VECTOR_SIZES)}"
            )
        return model

    async def _resolve_models_async(self, collection_name: str) -> list[type[db_models.BaseDocumentChunk]]:
        """컬렉션이 사용하는 청크 테이블 후보를 반환합니다.

        레지스트리(`collections.vector_size`)를 알면 테이블 하나만 보면 되고,
        레지스트리 행이 없으면 어느 차원인지 알 수 없으므로 전체 테이블을 순회합니다.

        Args:
            collection_name (str): 컬렉션 이름

        Returns:
            list[type[db_models.BaseDocumentChunk]]: 조회 대상 테이블 모델 리스트
        """

        db_manager = await get_db_manager()
        query = (db_models.Collection
                 .select(db_models.Collection.vector_size)
                 .where(db_models.Collection.name == collection_name))
        rows = await db_manager.execute_query(query)
        for row in rows:
            model = db_models.DOCUMENT_CHUNK_MODELS.get(row.vector_size)
            if model is not None:
                return [model]
        return list(db_models.DOCUMENT_CHUNK_MODELS.values())

    def _payload_columns(self, model: type[db_models.BaseDocumentChunk]) -> list:
        """payload 구성에 필요한 컬럼만 반환합니다.

        `embedding`은 차원 수만큼의 실수 배열이라, 조회 때마다 함께 읽으면
        네트워크와 메모리를 크게 낭비합니다. 검색·조회 응답에는 쓰이지 않으므로 제외합니다.

        Args:
            model (type[db_models.BaseDocumentChunk]): 청크 테이블 모델

        Returns:
            list: SELECT 대상 컬럼 리스트
        """

        return [
            model.id,
            model.document_id,
            model.chunk_index,
            model.content,
            model.page,
            model.file_name,
            model.metadata,
        ]

    def _build_payload(self, row: db_models.BaseDocumentChunk) -> dict[str, Any]:
        """청크 행을 기존 Qdrant payload와 동일한 형태의 dict로 변환합니다.

        호출부가 `payload["document_id"]`처럼 평평한 dict를 기대하므로
        전용 컬럼과 `metadata` JSONB를 하나로 합쳐서 돌려줍니다.

        Args:
            row (db_models.BaseDocumentChunk): 청크 행

        Returns:
            dict[str, Any]: payload
        """

        payload = dict(row.metadata or {})
        payload.update({
            "document_id": row.document_id,
            "chunk_index": row.chunk_index,
            "content": row.content,
            "page": row.page,
            "file_name": row.file_name,
        })
        return payload

    def _apply_filter_conditions(
        self,
        query,
        model: type[db_models.BaseDocumentChunk],
        filter_conditions: Optional[dict[str, Any]],
    ):
        """payload 필터 조건을 쿼리에 적용합니다. (동등 비교)

        Args:
            query: peewee 쿼리
            model (type[db_models.BaseDocumentChunk]): 청크 테이블 모델
            filter_conditions (Optional[dict[str, Any]]): 필터 조건

        Returns:
            peewee 쿼리 (조건이 적용된 새 쿼리)
        """

        for key, value in (filter_conditions or {}).items():
            column = model._meta.fields.get(key)
            if column is not None:
                query = query.where(column == value)
            else:
                # 컬럼으로 승격되지 않은 payload 키는 metadata JSONB 안에서 찾는다
                query = query.where(model.metadata.contains({key: value}))
        return query

    def _lexical_expressions(self, model: type[db_models.BaseDocumentChunk], query_text: str):
        """어휘 검색용 tsvector/tsquery/랭크 표현식을 만듭니다.

        `to_tsvector('simple', content)` 표현식은 `app/utils/database.py`가 기동 시 만드는
        GIN 인덱스(`{table}_content_tsv_gin_idx`)와 **문자 그대로 같아야** 인덱스를 탑니다.
        설정 이름(`simple`)이나 대상 컬럼을 바꾸면 인덱스도 함께 바꿔야 합니다.

        Args:
            model (type[db_models.BaseDocumentChunk]): 청크 테이블 모델
            query_text (str): 사용자 질의문

        Returns:
            tuple: (매칭 조건 표현식, `ts_rank_cd` 표현식)
        """

        config_name = SQL(f"'{TEXT_SEARCH_CONFIG}'")
        tsvector = fn.to_tsvector(config_name, model.content)
        # plainto_tsquery는 사용자가 입력한 문장을 그대로 받아 AND 질의로 바꿔 줍니다.
        # to_tsquery와 달리 `&`, `!` 같은 연산자 문법을 해석하지 않으므로,
        # 질문 문장이 그대로 들어와도 구문 오류가 나지 않습니다. (질의문은 외부 입력입니다.)
        tsquery = fn.plainto_tsquery(config_name, query_text)

        # 학생 말투 → 규정 어휘 동의어를 **OR로** 덧붙입니다. ("기숙사" → "생활관")
        #
        # 질의문에 확장어를 이어 붙이면 안 됩니다. plainto_tsquery가 AND로 묶기 때문에
        # "기숙사 생활관 퇴사"는 세 단어를 모두 가진 청크만 걸려 어휘 검색이 죽습니다.
        # tsquery끼리 `||`로 합치면 결과가 원래 집합의 **상위집합**이라 recall이
        # 줄어들 수 없습니다. 확장어는 각각 단어 하나라 plainto_tsquery로 안전합니다.
        # `|` 연산자를 쓰면 안 됩니다. peewee가 SQL 불리언 `OR`로 렌더링하는데,
        # tsquery끼리의 OR는 `||` 연산자입니다. `OR`로 나가면 타입 오류가 납니다.
        for term in lexical_expansion_terms(query_text):
            tsquery = Expression(tsquery, "||", fn.plainto_tsquery(config_name, term))

        # ts_rank_cd는 커버 밀도(cover density) 기반 랭킹이라 질의어가 가깝게 모여 있는
        # 청크를 높게 봅니다. 조문처럼 짧은 텍스트에서 ts_rank보다 변별력이 좋습니다.
        return Expression(tsvector, "@@", tsquery), fn.ts_rank_cd(tsvector, tsquery)

    async def create_collection_async(
        self,
        collection_name: str,
        vector_size: int,
        distance: str = DEFAULT_DISTANCE,
    ):
        """컬렉션을 생성합니다.

        차원별 테이블은 기동 시 이미 만들어져 있고 컬렉션은 `collection_name` 컬럼으로만
        구분되므로, 실제로 만들 물리 객체는 없습니다. 이 메서드는 해당 차원을 저장할 수
        있는지만 검증하고 반환합니다. (컬렉션 메타데이터는 호출부가 `collections`에 기록합니다.)

        Args:
            collection_name (str): 컬렉션 이름
            vector_size (int): 벡터 차원 크기
            distance (str): 거리 측정 방식 (코사인 고정, 호환용으로만 유지)

        Raises:
            ValueError: 지원하지 않는 차원인 경우
        """

        model = self._get_model(vector_size)

        if milvus_store_mod.is_milvus_enabled():
            await milvus_store_mod.ensure_document_collection_async(vector_size)

        logger.info(
            "컬렉션이 준비되었습니다. "
            f"(collection={collection_name}, vector_size={vector_size}, "
            f"table={model._meta.table_name}, distance={DEFAULT_DISTANCE}, "
            f"milvus={milvus_store_mod.is_milvus_enabled()})"
        )

    async def collection_exists_async(self, collection_name: str) -> bool:
        """컬렉션에 저장된 청크가 있는지 확인합니다.

        Qdrant와 달리 빈 컬렉션은 물리적 흔적이 남지 않으므로, 청크가 한 건도 없으면
        False를 반환합니다. 컬렉션 자체의 존재 여부는 `collections` 테이블이 기준입니다.

        Args:
            collection_name (str): 컬렉션 이름

        Returns:
            bool: 청크 존재 여부
        """

        db_manager = await get_db_manager()
        for model in await self._resolve_models_async(collection_name):
            query = (model.select(model.id)
                     .where(model.collection_name == collection_name)
                     .limit(1))
            rows = await db_manager.execute_query(query)
            for _ in rows:
                return True
        return False

    async def delete_collection_async(self, collection_name: str):
        """컬렉션의 모든 청크를 삭제합니다.

        컬렉션이 어느 차원 테이블을 쓰는지와 무관하게 남는 행이 없도록
        모든 차원 테이블에서 삭제합니다. (임베딩 모델을 바꿔 재색인한 이력이 있을 수 있음)

        Args:
            collection_name (str): 컬렉션 이름
        """

        db_manager = await get_db_manager()
        deleted = 0
        for model in db_models.DOCUMENT_CHUNK_MODELS.values():
            query = model.delete().where(model.collection_name == collection_name)
            deleted += await db_manager.execute_query(query, timeout=BULK_QUERY_TIMEOUT) or 0

        if milvus_store_mod.is_milvus_enabled():
            await milvus_store_mod.delete_document_collection_async(collection_name)

        logger.info(f"컬렉션이 삭제되었습니다. (collection={collection_name}, chunks={deleted})")

    async def upsert_points_async(self, collection_name: str, points: list[dict[str, Any]]):
        """벡터 포인트를 추가/업데이트합니다.

        Args:
            collection_name (str): 컬렉션 이름
            points (list[dict[str, Any]]): 포인트 리스트
                (`{"id": str, "vector": list[float], "payload": dict}`)

        Raises:
            ValueError: 지원하지 않는 차원의 벡터가 포함된 경우
        """

        if not points:
            return

        db_manager = await get_db_manager()

        # 한 컬렉션은 한 차원만 쓰지만, 방어적으로 차원별로 나눠 담는다
        rows_by_model: dict[type[db_models.BaseDocumentChunk], list[dict[str, Any]]] = {}
        points_by_size: dict[int, list[dict[str, Any]]] = {}
        for point in points:
            vector = point.get("vector") or []
            payload = point.get("payload") or {}
            model = self._get_model(len(vector))
            point_id = point.get("id") or uuid.uuid4()

            rows_by_model.setdefault(model, []).append({
                "id": point_id,
                "collection_name": collection_name,
                "document_id": payload.get("document_id"),
                "chunk_index": payload.get("chunk_index", 0),
                "content": payload.get("content", ""),
                "page": payload.get("page"),
                "file_name": payload.get("file_name"),
                "metadata": {
                    key: value for key, value in payload.items()
                    if key not in PAYLOAD_COLUMN_KEYS
                } or None,
                "embedding": vector,
            })
            points_by_size.setdefault(len(vector), []).append({
                "id": str(point_id),
                "vector": vector,
                "payload": payload,
            })

        for model, rows in rows_by_model.items():
            for offset in range(0, len(rows), UPSERT_BATCH_SIZE):
                batch = rows[offset:offset + UPSERT_BATCH_SIZE]
                query = (model.insert_many(batch)
                         # 같은 ID로 다시 넣으면 덮어쓴다 (Qdrant upsert와 동일한 의미)
                         .on_conflict(
                             conflict_target=[model.id],
                             preserve=[
                                 model.collection_name,
                                 model.document_id,
                                 model.chunk_index,
                                 model.content,
                                 model.page,
                                 model.file_name,
                                 model.metadata,
                                 model.embedding,
                             ],
                         ))
                await db_manager.execute_query(query, timeout=BULK_QUERY_TIMEOUT)

        # milvus.enabled면 dual-write. PG는 항상 써서 롤백·어휘검색 원천을 유지한다.
        if milvus_store_mod.is_milvus_enabled():
            for vector_size, milvus_points in points_by_size.items():
                await milvus_store_mod.upsert_document_chunks_async(
                    vector_size, collection_name, milvus_points,
                )

        logger.debug(f"{len(points)}개의 포인트가 저장되었습니다. (collection={collection_name})")

    async def search_async(
        self,
        collection_name: str,
        query_vector: list[float],
        limit: int = 5,
        score_threshold: Optional[float] = None,
        filter_conditions: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """벡터 유사도 검색을 수행합니다.

        pgvector의 코사인 거리(`<=>`) 기준 HNSW 인덱스를 사용합니다.
        거리는 0(동일)~2(정반대) 범위라 유사도 점수는 `1 - distance`로 환산합니다.

        Args:
            collection_name (str): 컬렉션 이름
            query_vector (list[float]): 검색 벡터
            limit (int): 결과 개수 (기본값: 5)
            score_threshold (Optional[float]): 최소 유사도 점수
            filter_conditions (Optional[dict[str, Any]]): 필터 조건 (동등 비교)

        Returns:
            list[dict[str, Any]]: 검색 결과 리스트 (`{"id", "score", "payload"}`)

        Raises:
            ValueError: 지원하지 않는 차원의 검색 벡터인 경우
        """

        vector_size = len(query_vector)
        self._get_model(vector_size)

        if milvus_store_mod.is_milvus_enabled():
            return await milvus_store_mod.search_document_chunks_async(
                vector_size,
                collection_name,
                query_vector,
                limit=limit,
                score_threshold=score_threshold,
                filter_conditions=filter_conditions,
            )

        model = self._get_model(vector_size)
        db_manager = await get_db_manager()

        distance = model.embedding.cosine_distance(query_vector)
        query = (model
                 .select(*self._payload_columns(model), distance.alias("distance"))
                 .where(model.collection_name == collection_name)
                 .order_by(distance)
                 .limit(limit))

        query = self._apply_filter_conditions(query, model, filter_conditions)

        rows = await db_manager.execute_query(query, timeout=SEARCH_QUERY_TIMEOUT)

        results = []
        for row in rows:
            score = 1 - row.distance
            if score_threshold is not None and score < score_threshold:
                continue
            results.append({
                "id": str(row.id),
                "score": score,
                "payload": self._build_payload(row),
            })
        return results

    async def hybrid_search_async(
        self,
        collection_name: str,
        query_text: str,
        query_vector: list[float],
        limit: int = DEFAULT_HYBRID_TOP_K,
        score_threshold: Optional[float] = None,
        filter_conditions: Optional[dict[str, Any]] = None,
        dense_weight: float = DENSE_WEIGHT,
        lexical_weight: float = LEXICAL_WEIGHT,
        candidate_multiplier: int = CANDIDATE_MULTIPLIER,
    ) -> list[dict[str, Any]]:
        """dense + 어휘 hybrid 검색을 수행합니다. (학칙·규정 RAG용)

        `search_async`(dense 전용)를 대체하지 않고 별도로 제공합니다.
        기존 범용 문서 RAG 호출부는 그대로 dense 검색을 쓰고, 규정 검색만 이 메서드를 씁니다.

        **동작**

        1. dense: pgvector 또는 Milvus(`milvus.enabled`) 코사인 상위 `limit * candidate_multiplier`건
        2. 어휘: `to_tsvector('simple', content) @@ plainto_tsquery('simple', 질의)` 매칭 후
           `ts_rank_cd` 상위 동수 (**항상 Postgres**)
        3. 두 결과를 각각 min-max 정규화한 뒤 `0.55 * dense + 0.45 * lexical`로 가중합
        4. 질의에 문서번호(`3-1-10`)·조항(`제15조`)이 있으면 메타데이터 정확 일치 청크에 가산점
        5. 상위 `limit`건 반환

        가중치 0.55/0.45와 기본 `limit=12`는 `rag-test/docs/rag_test_report.md`의
        골드셋 48문항 측정값입니다. dense 전용(E3) 83.3% / Recall 81.3% 대비
        이 구성(E4)이 91.7% / Recall 89.6%였습니다.

        어휘 검색은 한국어 형태소 분석기 없이 `simple` 설정으로 동작하므로
        조사·복합명사를 분해하지 못합니다. 자세한 한계는 `TEXT_SEARCH_CONFIG`를 보세요.

        Args:
            collection_name (str): 컬렉션 이름
            query_text (str): 사용자 질의문 (어휘 검색·boost 신호 추출에 사용)
            query_vector (list[float]): 질의 임베딩 벡터
            limit (int): 반환할 결과 수 (Default: 12)
            score_threshold (Optional[float]): 최소 융합 점수 (정규화된 0~1 스케일 기준)
            filter_conditions (Optional[dict[str, Any]]): 필터 조건 (동등 비교)
            dense_weight (float): dense 가중치 (Default: 0.55)
            lexical_weight (float): 어휘 가중치 (Default: 0.45)
            candidate_multiplier (int): 검색기별 후보 배수 (Default: 4)

        Returns:
            list[dict[str, Any]]: 검색 결과 (`{"id", "score", "payload"}`)
                `payload`에는 디버깅용으로 `dense_score`/`lexical_score`/`boost`가 함께 담깁니다.

        Raises:
            ValueError: 지원하지 않는 차원의 검색 벡터인 경우
        """

        model = self._get_model(len(query_vector))
        db_manager = await get_db_manager()

        candidate_limit = max(limit * max(candidate_multiplier, 1), limit)

        # 1) dense 후보
        payloads: dict[str, dict[str, Any]] = {}
        dense_scores: dict[str, float] = {}

        if milvus_store_mod.is_milvus_enabled():
            dense_hits = await milvus_store_mod.search_document_chunks_async(
                len(query_vector),
                collection_name,
                query_vector,
                limit=candidate_limit,
                score_threshold=None,
                filter_conditions=filter_conditions,
            )
            for hit in dense_hits:
                point_id = str(hit["id"])
                payloads[point_id] = hit["payload"]
                dense_scores[point_id] = float(hit["score"])
        else:
            distance = model.embedding.cosine_distance(query_vector)
            dense_query = (model
                           .select(*self._payload_columns(model), distance.alias("distance"))
                           .where(model.collection_name == collection_name)
                           .order_by(distance)
                           .limit(candidate_limit))
            dense_query = self._apply_filter_conditions(dense_query, model, filter_conditions)
            dense_rows = await db_manager.execute_query(dense_query, timeout=SEARCH_QUERY_TIMEOUT)
            for row in dense_rows:
                point_id = str(row.id)
                payloads[point_id] = self._build_payload(row)
                dense_scores[point_id] = 1 - row.distance

        # 2) 어휘 후보 (항상 Postgres)
        match_expression, rank = self._lexical_expressions(model, query_text or "")
        lexical_query = (model
                         .select(*self._payload_columns(model), rank.alias("rank"))
                         .where(model.collection_name == collection_name)
                         .where(match_expression)
                         .order_by(rank.desc())
                         .limit(candidate_limit))
        lexical_query = self._apply_filter_conditions(lexical_query, model, filter_conditions)
        lexical_rows = await db_manager.execute_query(lexical_query, timeout=SEARCH_QUERY_TIMEOUT)

        lexical_scores: dict[str, float] = {}

        for row in lexical_rows:
            point_id = str(row.id)
            payloads.setdefault(point_id, self._build_payload(row))
            lexical_scores[point_id] = float(row.rank or 0.0)

        # 4) 문서번호·조항 정확 일치 가산점 (리포트 6.3절 부칙 청크 과다 노출 완화)
        signals = parse_query_signals(query_text or "")
        boosts = {
            point_id: compute_boost(payload, signals)
            for point_id, payload in payloads.items()
        }

        # 5) 융합·재순위
        fused = fuse_hybrid_scores(
            dense_scores=dense_scores,
            lexical_scores=lexical_scores,
            boosts=boosts,
            dense_weight=dense_weight,
            lexical_weight=lexical_weight,
            limit=limit,
        )

        results = []
        for item in fused:
            if score_threshold is not None and item["score"] < score_threshold:
                continue

            payload = dict(payloads[item["id"]])
            payload.update({
                "dense_score": item["dense_score"],
                "lexical_score": item["lexical_score"],
                "boost": item["boost"],
            })
            results.append({
                "id": item["id"],
                "score": item["score"],
                "payload": payload,
            })

        logger.debug(
            "hybrid 검색을 수행했습니다. "
            f"(collection={collection_name}, dense={len(dense_scores)}, "
            f"lexical={len(lexical_scores)}, returned={len(results)}, "
            f"milvus={milvus_store_mod.is_milvus_enabled()})"
        )
        return results

    async def delete_points_by_document_id_async(self, collection_name: str, document_id: int):
        """특정 문서 ID의 모든 청크를 삭제합니다.

        Args:
            collection_name (str): 컬렉션 이름
            document_id (int): 문서 ID
        """

        db_manager = await get_db_manager()
        for model in db_models.DOCUMENT_CHUNK_MODELS.values():
            query = (model.delete()
                     .where(model.collection_name == collection_name)
                     .where(model.document_id == document_id))
            await db_manager.execute_query(query, timeout=BULK_QUERY_TIMEOUT)

        if milvus_store_mod.is_milvus_enabled():
            await milvus_store_mod.delete_document_by_id_async(collection_name, document_id)

        logger.debug(f"청크가 삭제되었습니다. (collection={collection_name}, document_id={document_id})")

    async def get_points_by_document_id_async(
        self,
        collection_name: str,
        document_id: int,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """특정 문서 ID의 모든 청크를 조회합니다.

        Args:
            collection_name (str): 컬렉션 이름
            document_id (int): 문서 ID
            limit (int): 최대 조회 개수 (기본값: 1000)

        Returns:
            list[dict[str, Any]]: payload 리스트 (`chunk_index` 오름차순)
        """

        db_manager = await get_db_manager()
        for model in await self._resolve_models_async(collection_name):
            query = (model.select(*self._payload_columns(model))
                     .where(model.collection_name == collection_name)
                     .where(model.document_id == document_id)
                     .order_by(model.chunk_index)
                     .limit(limit))
            rows = await db_manager.execute_query(query)
            payloads = [self._build_payload(row) for row in rows]
            if payloads:
                return payloads
        return []

    async def get_collection_info_async(self, collection_name: str) -> dict[str, Any]:
        """컬렉션 정보를 조회합니다.

        기존 Qdrant 응답 구조를 유지합니다. 벡터 차원은 실제 저장 테이블이 아니라
        레지스트리(`collections.vector_size`)에서 읽습니다. 청크가 아직 없어도
        컬렉션 설정을 알 수 있어야 하기 때문입니다.

        Args:
            collection_name (str): 컬렉션 이름

        Returns:
            dict[str, Any]: 컬렉션 정보
        """

        db_manager = await get_db_manager()

        query = (db_models.Collection
                 .select(db_models.Collection.vector_size)
                 .where(db_models.Collection.name == collection_name))
        rows = await db_manager.execute_query(query)
        vector_size = next((row.vector_size for row in rows), None)

        models = ([db_models.DOCUMENT_CHUNK_MODELS[vector_size]]
                  if vector_size in db_models.DOCUMENT_CHUNK_MODELS
                  else list(db_models.DOCUMENT_CHUNK_MODELS.values()))

        points_count = 0
        for model in models:
            count_query = (model
                           .select(fn.COUNT(model.id).alias("count"))
                           .where(model.collection_name == collection_name))
            count_rows = await db_manager.execute_query(count_query)
            points_count += next((row.count for row in count_rows), 0)

        return {
            "points_count": points_count,
            "vectors_count": points_count,
            # 별도 벡터 DB가 아니라 DB 트랜잭션으로 즉시 반영되므로 색인 대기 상태가 없다
            "status": "green",
            "config": {
                "params": {
                    "vectors": {
                        "size": vector_size,
                        "distance": DEFAULT_DISTANCE,
                    }
                }
            }
        }


_vector_store_manager: Optional[VectorStoreManager] = None


def get_vector_store_manager() -> VectorStoreManager:
    """Vector Store Manager 인스턴스를 반환합니다.

    Returns:
        VectorStoreManager: Vector Store Manager 인스턴스
    """

    global _vector_store_manager
    if _vector_store_manager is None:
        _vector_store_manager = VectorStoreManager()
    return _vector_store_manager
