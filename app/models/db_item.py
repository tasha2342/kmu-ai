from datetime import datetime

from uuid import UUID

from typing import Optional, Union, Any, Literal, get_origin, get_args, TYPE_CHECKING

from pydantic import BaseModel, Field, field_serializer

from app.models.enum import (
    ModelProvider,
    ModelType,
    ModelStatus,
    ModelUsageStatus,
    GraphIndexStatus,
    DocumentStatus,
    Language,
    FaqStatus,
    FaqVisibility,
    VectorStatus,
    SourceType,
    ChatIntent,
    ChatSessionStatus,
    ChatRole,
    UnansweredReason,
    ReviewStatus,
    IngestionStatus,
    IngestionItemStatus,
    MaskingTargetField,
    MaskingMethod,
)
from app.models.cost import (
    TextGenerationCost,
    EmbeddingCost,
    RerankCost,
    TextGenerationUsage,
    EmbeddingUsage,
    RerankUsage,
)
import app.utils.common as util

if TYPE_CHECKING:
    import app.models.database as db_models


class BaseItem(BaseModel):
    """데이터베이스 아이템을 위한 기본 클래스"""
    
    @classmethod
    def from_db(cls, db_instance: "db_models.BaseModel") -> Optional["BaseItem"]:
        """Peewee 모델 인스턴스를 Pydantic 모델로 자동 변환합니다.
        
        dict 값을 BaseModel 타입으로 자동 파싱합니다.
        Union 타입의 경우 각 타입에 맞게 자동으로 파싱을 시도합니다.
        
        Args:
            db_instance (db_models.BaseModel): 변환할 Peewee 모델 인스턴스

        Returns:
            Optional["BaseItem"]: 변환된 Pydantic 모델 인스턴스 또는 None
        """
        
        import app.models.database as db_models
        
        if not db_instance:
            return None

        data = {}
        for field_name, field_info in cls.model_fields.items():
            if hasattr(db_instance, field_name):
                value = getattr(db_instance, field_name)
                
                if isinstance(value, db_models.BaseModel):
                    # ForeignKey 필드 처리 (Peewee 모델 인스턴스 -> ID)
                    value = value.id
                elif isinstance(value, db_models.Point5181):
                    # Point5181 좌표 처리
                    value = {
                        "x": value.x,
                        "y": value.y
                    }
                elif isinstance(value, dict):
                    # dict를 BaseModel로 자동 파싱
                    field_type = field_info.annotation
                    
                    # Union 타입인 경우 처리
                    if get_origin(field_type) is Union:
                        field_type = cls._find_best_matching_type(value, field_info.annotation)
                    
                    value = cls._parse_dict_to_model(value, field_type)
                
                data[field_name] = value
        return cls(**data)
    
    @staticmethod
    def _find_best_matching_type(value: dict, union_type: type) -> type:
        """Union 타입에서 dict에 가장 적합한 타입을 찾습니다.
        
        상속 관계가 있는 경우 더 구체적인 타입(자식 클래스)을 우선합니다.
        필드 매칭도를 계산하여 가장 적합한 타입을 선택합니다.
        
        Args:
            value (dict): 확인할 dict 값
            union_type (type): Union 타입
            
        Returns:
            type: 가장 적합한 타입
        """
        
        args = get_args(union_type)
        candidates = []
        
        for arg_type in args:
            if arg_type is type(None):
                continue
                
            if isinstance(arg_type, type) and issubclass(arg_type, BaseModel):
                # 필수 필드가 모두 있는지 확인
                if BaseItem._has_required_fields(value, arg_type):
                    # 모든 필드(필수 + 선택) 개수 계산
                    all_fields = set(arg_type.model_fields.keys())
                    dict_keys = set(value.keys())
                    
                    # 매칭되는 필드 개수
                    matching_fields = len(all_fields & dict_keys)
                    # dict에만 있는 추가 필드 개수 (페널티)
                    extra_fields = len(dict_keys - all_fields)
                    # 모델에만 있는 누락 필드 개수 (페널티)
                    missing_fields = len(all_fields - dict_keys)
                    
                    # 점수 계산: 매칭 필드는 +, 추가/누락 필드는 -
                    score = matching_fields - extra_fields - missing_fields
                    
                    candidates.append((arg_type, score, len(all_fields)))
        
        if not candidates:
            # 적합한 타입이 없으면 원래 union_type 반환
            return union_type
        
        # 1. 점수가 높은 순
        # 2. 점수가 같으면 필드 개수가 많은 순 (더 구체적인 타입)
        candidates.sort(key=lambda x: (x[1], x[2]), reverse=True)
        
        return candidates[0][0]
    
    @staticmethod
    def _has_required_fields(value: dict, model_type: type[BaseModel]) -> bool:
        """dict가 BaseModel의 필수 필드를 모두 포함하는지 확인합니다.
        
        Args:
            value (dict): 확인할 dict 값
            model_type (type[BaseModel]): 확인할 BaseModel 타입
            
        Returns:
            bool: 필수 필드가 모두 있으면 True, 아니면 False
        """
        
        if not value:
            return False
            
        # BaseModel의 필수 필드 확인
        required_fields = set()
        for field_name, field_info in model_type.model_fields.items():
            # default 값이 없고, None이 허용되지 않는 필드가 필수 필드
            if field_info.is_required():
                required_fields.add(field_name)
        
        # dict의 키가 필수 필드를 모두 포함하는지 확인
        return required_fields.issubset(value.keys())
    
    @staticmethod
    def _parse_dict_to_model(value: dict, field_type: type[Any]) -> Union[BaseModel, dict]:
        """dict 값을 필드 타입에 맞는 BaseModel로 파싱합니다.
        
        Args:
            value (dict): 파싱할 dict 값
            field_type (type[Any]): 필드의 타입 힌트
            
        Returns:
            파싱된 BaseModel 인스턴스 또는 원본 dict
        """
        
        if not value:
            return value
        
        origin = get_origin(field_type)
        if origin is Union:
            # Union 타입인 경우 가장 적합한 타입 찾기
            field_type = BaseItem._find_best_matching_type(value, field_type)
            
            # 최적 타입으로 파싱 시도
            if isinstance(field_type, type) and issubclass(field_type, BaseModel):
                if BaseItem._has_required_fields(value, field_type):
                    try:
                        return field_type(**value)
                    except (TypeError, ValueError):
                        pass
        elif isinstance(field_type, type) and issubclass(field_type, BaseModel):
            # 일반 BaseModel 타입인 경우 필수 필드 확인 후 파싱
            if BaseItem._has_required_fields(value, field_type):
                try:
                    return field_type(**value)
                except (TypeError, ValueError):
                    pass
        
        # 파싱할 수 없는 경우 원본 반환
        return value

class Point5181(BaseModel):
    """ESPG:5181 좌표계"""

    x: float = Field(
        ...,
        description="동서 방향 거리(meter)입니다.",
        examples=[946763.2477569042]
    )
    y: float = Field(
        ...,
        description="남북 방향 거리(meter)입니다.",
        examples=[1946980.780639075]
    )


class Model(BaseItem):
    """모델 정보 아이템"""

    name: str = Field(
        ...,
        description="모델명입니다. (사용자 정의)",
        examples=["gpt-4o-mini"]
    )
    provider: ModelProvider = Field(
        ...,
        description="모델 제공자입니다.",
        examples=list(ModelProvider)
    )
    model_id: str = Field(
        ...,
        description="모델 ID입니다. (제공자의 모델 ID)",
        examples=["gpt-4o-mini"]
    )
    model_type: ModelType = Field(
        ...,
        description="모델 타입입니다.",
        examples=list(ModelType)
    )
    api_base: Optional[str] = Field(
        None,
        description="API Base URL입니다. (Local, OpenAI Compatible용)",
        examples=["http://192.168.0.100:8000/v1"]
    )
    api_key: Optional[str] = Field(
        None,
        description="API Key입니다.",
        examples=["jd-TVlMaVY5aXduSkYyUENGQnJSYkhJSjVRZDExMDdiNGUtOTM2"],
        exclude=True
    )
    description: Optional[str] = Field(
        None,
        description="모델 설명입니다.",
        examples=["OpenAI의 최신 모델"]
    )
    cost: Optional[Union[
        TextGenerationCost,
        EmbeddingCost,
        RerankCost,
    ]] = Field(
        None,
        description=(
            "모델 비용입니다.  \n"
            "모델 타입에 따라 비용 구조가 다릅니다.  \n"
            "text_generation: {input_tokens, cached_input_tokens, output_tokens}  \n"
            "embedding: {single_tokens, batch_tokens}  \n"
            "rerank: {searches}"
        ),
        examples=[
            TextGenerationCost(input_tokens=2.50, cached_input_tokens=0.25, output_tokens=15.00),
            EmbeddingCost(single_tokens=0.20, batch_tokens=0.10),
            RerankCost(searches=2.00)
        ]
    )
    status: ModelStatus = Field(
        ...,
        description="모델 상태입니다.",
        examples=list(ModelStatus)
    )
    error_message: Optional[str] = Field(
        None,
        description="오류 메시지입니다.",
        examples=["Failed to connect to model provider"]
    )
    config: Optional[dict] = Field(
        default_factory=dict,
        description="모델 설정입니다. (JSON 형식)",
        examples=[{"temperature": 0.7}]
    )
    created_at: datetime = Field(
        ...,
        description="모델이 등록된 날짜입니다.",
        examples=[util.get_now()]
    )
    updated_at: datetime = Field(
        ...,
        description="모델이 마지막으로 수정된 날짜입니다.",
        examples=[util.get_now()]
    )
    
    
    @field_serializer("created_at", "updated_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

class ModelUsage(BaseItem):
    """모델 사용량 아이템"""

    id: int = Field(
        ...,
        description="사용량 ID입니다.",
        examples=[1]
    )
    user_name: str = Field(
        ...,
        description="사용자명입니다.",
        examples=["admin"]
    )
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
    usage: Optional[Union[
        TextGenerationUsage,
        EmbeddingUsage,
        RerankUsage,
    ]] = Field(
        None,
        description=(
            "사용량입니다.  \n"
            "모델 타입에 따라 구조가 다릅니다.  \n"
            "text_generation: {input_tokens, cached_input_tokens, output_tokens}  \n"
            "embedding: {single_tokens, batch_tokens}  \n"
            "rerank: {}"
        ),
        examples=[
            TextGenerationUsage(input_tokens=100, cached_input_tokens=50, output_tokens=50),
            EmbeddingUsage(single_tokens=100, batch_tokens=50),
            RerankUsage(),
        ]
    )
    request_id: Optional[str] = Field(
        None,
        description="요청 ID입니다.",
        examples=[f"chatcmpl-fkrea1235lsdmfkoi2nmfsdo"]
    )
    latency_ms: int = Field(
        0,
        description="응답 시간(ms)입니다.",
        examples=[1500]
    )
    status: ModelUsageStatus = Field(
        ...,
        description="요청 상태입니다.",
        examples=list(ModelUsageStatus)
    )
    error_message: Optional[str] = Field(
        None,
        description="오류 메시지입니다.",
        examples=["Rate limit exceeded"]
    )
    metadata: Optional[dict] = Field(
        None,
        description="추가 메타데이터입니다.",
        examples=[{"stream": True}]
    )
    created_at: datetime = Field(
        ...,
        description="생성 날짜입니다.",
        examples=[util.get_now()]
    )
    
    
    @field_serializer("created_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)


class Collection(BaseItem):
    """컬렉션 아이템"""
    
    name: str = Field(
        ...,
        description="컬렉션 이름입니다.",
        examples=["test"]
    )
    user_name: str = Field(
        ...,
        description="생성자의 사용자명입니다.",
        examples=["admin"]
    )
    embedding_model: str = Field(
        ...,
        description="임베딩에 사용된 모델명입니다.",
        examples=["text-embedding-3-small"]
    )
    vector_size: int = Field(
        ...,
        description="벡터 차원 크기입니다.",
        examples=[1536]
    )
    description: Optional[str] = Field(
        None,
        description="컬렉션 설명입니다."
    )
    access_scope: Optional[str] = Field(
        None,
        description=(
            "접근 가능 범위입니다.  \n"
            "- **NULL**: 모두 접근 가능  \n"
            "- **group:{group_name}**: 해당 그룹에 속한 사용자만 접근 가능  \n"
            "- **user:{user_name}**: 해당 사용자만 접근 가능  \n"
        ),
        examples=[None, "group:admin", "user:admin"]
    )
    is_system: bool = Field(
        False,
        description=(
            "시스템 전용 컬렉션 여부입니다.  \n"
            "목록/상세 조회 시 기본적으로 제외됩니다."
        ),
        examples=[False, True]
    )
    is_active: bool = Field(
        True,
        description="검색(RAG) 활성 여부입니다. False면 챗봇 검색 대상에서 제외할 수 있습니다.",
        examples=[True, False]
    )
    chunk_size: int = Field(
        1000,
        description="문서 청킹 목표 크기(문자 수)입니다.",
        examples=[1000]
    )
    chunk_overlap: int = Field(
        100,
        description="청크 간 오버랩 크기(문자 수)입니다.",
        examples=[100]
    )
    top_k: int = Field(
        5,
        description="검색 시 반환할 상위 결과 수입니다.",
        examples=[5]
    )
    similarity_threshold: float = Field(
        0.35,
        description="검색 최소 유사도 점수입니다.",
        examples=[0.35]
    )
    graph_index_status: GraphIndexStatus = Field(
        GraphIndexStatus.NO_INDEXED,
        description="그래프 인덱싱 상태입니다.",
        examples=list(GraphIndexStatus)
    )
    graph_index_error: Optional[str] = Field(
        None,
        description="그래프 인덱싱 오류 메시지입니다."
    )
    graph_indexed_at: Optional[datetime] = Field(
        None,
        description="마지막 그래프 인덱싱 날짜입니다."
    )
    created_at: datetime = Field(
        ...,
        description="생성 날짜입니다.",
        examples=[util.get_now()]
    )
    updated_at: datetime = Field(
        ...,
        description="수정 날짜입니다.",
        examples=[util.get_now()]
    )
    
    
    @field_serializer("created_at", "updated_at", "graph_indexed_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

class Document(BaseItem):
    """문서 아이템"""

    id: int = Field(
        ...,
        description="문서 ID입니다.",
        examples=[1]
    )
    collection_name: str = Field(
        ...,
        description="컬렉션 이름입니다.",
        examples=["test"]
    )
    user_name: str = Field(
        ...,
        description="생성자의 사용자명입니다.",
        examples=["admin"]
    )
    file_name: str = Field(
        ...,
        description="파일명입니다.",
        examples=["document.pdf"]
    )
    file_path: str = Field(
        ...,
        description="S3 파일 경로입니다.",
        examples=["documents/2026/01/08/document_12345.pdf"]
    )
    file_size: int = Field(
        ...,
        description="파일 크기(bytes)입니다.",
        examples=[1048576]
    )
    file_type: str = Field(
        ...,
        description="파일 타입입니다.",
        examples=["pdf"]
    )
    chunk_count: int = Field(
        0,
        description="청크 개수입니다.",
        examples=[25]
    )
    metadata: Optional[dict] = Field(
        None,
        description="문서 메타데이터입니다.",
        examples=[{
            "title": "프로젝트 문서",
            "author": "홍길동",
            "created_date": "2026-01-08",
            "modified_date": "2026-01-08",
            "page_count": 10,
            "file_type": "pdf"
        }]
    )
    status: DocumentStatus = Field(
        ...,
        description="문서 처리 상태입니다.",
        examples=list(DocumentStatus)
    )
    error_message: Optional[str] = Field(
        None,
        description="오류 메시지입니다.",
        examples=["Failed to parse document"]
    )
    created_at: datetime = Field(
        ...,
        description="등록 날짜입니다.",
        examples=[util.get_now()]
    )
    updated_at: datetime = Field(
        ...,
        description="수정 날짜입니다.",
        examples=[util.get_now()]
    )
    
    
    @field_serializer("created_at", "updated_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

class BaseDocumentChunk(BaseItem):
    """문서 청크 아이템 (차원 공통)

    `embedding` 벡터 본문은 담지 않습니다. 차원 수만큼의 실수 배열이라
    목록 조회 응답이 급격히 커지고, 클라이언트가 쓸 일도 없기 때문입니다.
    """

    id: UUID = Field(
        ...,
        description="청크 ID입니다."
    )
    collection_name: str = Field(
        ...,
        description="컬렉션 이름입니다.",
        examples=["test"]
    )
    document_id: int = Field(
        ...,
        description="문서 ID입니다.",
        examples=[1]
    )
    chunk_index: int = Field(
        ...,
        description="문서 내 청크 순번입니다.",
        examples=[0, 1, 2]
    )
    content: str = Field(
        ...,
        description="청크 본문입니다."
    )
    page: Optional[int] = Field(
        None,
        description="원본 페이지 번호입니다.",
        examples=[1]
    )
    file_name: Optional[str] = Field(
        None,
        description="원본 파일명입니다.",
        examples=["document.pdf"]
    )
    metadata: Optional[dict] = Field(
        None,
        description="청크 메타데이터입니다. (컬럼으로 승격되지 않은 나머지 payload)",
        examples=[{"total_chunks": 25, "title": "프로젝트 문서"}]
    )
    created_at: datetime = Field(
        ...,
        description="생성 날짜입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("created_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

class DocumentChunk384(BaseDocumentChunk):
    """384차원 문서 청크 아이템"""

class DocumentChunk768(BaseDocumentChunk):
    """768차원 문서 청크 아이템"""

class DocumentChunk1024(BaseDocumentChunk):
    """1024차원 문서 청크 아이템"""

class DocumentChunk1536(BaseDocumentChunk):
    """1536차원 문서 청크 아이템"""

class DocumentChunk3072(BaseDocumentChunk):
    """3072차원 문서 청크 아이템"""

class Prompt(BaseItem):
    """프롬프트"""
    
    id: int = Field(
        ...,
        description="프롬프트 ID입니다.",
        examples=[1, 2, 3]
    )
    name: str = Field(
        ...,
        description="프롬프트 이름입니다.",
        examples=["기본 시스템 프롬프트", "코드 리뷰 프롬프트"]
    )
    description: Optional[str] = Field(
        None,
        description="프롬프트 설명입니다.",
        examples=["일반적인 대화를 위한 기본 시스템 프롬프트"]
    )
    content: str = Field(
        ...,
        description=(
            "프롬프트 내용입니다. {{변수명}} 형태로 변수를 사용할 수 있습니다.  \n"
            "사용 가능한 변수는 다음과 같습니다:  \n"
            "- {{now:포맷}}: 현재 날짜와 시간 (포맷은 C# 형식을 사용합니다.)  \n"
            "  - https://learn.microsoft.com/ko-kr/dotnet/standard/base-types/custom-date-and-time-format-strings  \n"
            "  - {{now}}는 기본 포맷인 `yyyy-MM-dd HH:mm:ss`로 치환됩니다.  \n"
			"- {{user_name}}: 사용자 이름  \n"
			"- {{user_id}}: 사용자 ID  \n"
			"- {{변수명}}: 프롬프트 렌더링 API의 variables 파라미터로 전달된 커스텀 변수들  \n"
		),
        examples=[
            (
                "당신은 유용한 AI 어시스턴트입니다.  \n"
                "현재 시간은 {{now:yyyy-MM-dd HH:mm:ss}} 입니다.  \n"
                "당신이 대화하는 사용자의 이름은 {{user_name}} 입니다."
			)
		]
    )
    current_version: int = Field(
        ...,
        description="현재 버전 번호입니다.",
        examples=[1, 2, 3]
    )
    tags: Optional[list[str]] = Field(
        None,
        description="태그 배열입니다.",
        examples=[["일반"], ["PoC", "고객상담"]]
    )
    access_scope: Optional[str] = Field(
        None,
        description=(
            "접근 가능 범위입니다.  \n"
            "- **NULL**: 모두 접근 가능  \n"
            "- **group:{group_name}**: 해당 그룹에 속한 사용자만 접근 가능  \n"
            "- **user:{user_name}**: 해당 사용자만 접근 가능  \n"
        ),
        examples=[None, "group:admin", "user:admin"]
    )
    is_system: bool = Field(
        False,
        description=(
            "시스템 전용 프롬프트 여부입니다.  \n"
            "목록/상세 조회 시 기본적으로 제외됩니다."
        ),
        examples=[False, True]
    )
    created_by: str = Field(
        ...,
        description="생성자 ID입니다.",
        examples=["68b3b2e0-0d96-4dd9-a1c3-313d5e388628"]
    )
    created_at: datetime = Field(
        ...,
        description="생성 날짜입니다.",
        examples=[util.get_now()]
    )
    updated_at: datetime = Field(
        ...,
        description="수정 날짜입니다.",
        examples=[util.get_now()]
    )
    
    
    @field_serializer("created_at", "updated_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

class PromptVersion(BaseItem):
    """프롬프트 버전"""
    
    id: int = Field(
        ...,
        description="버전 ID입니다.",
        examples=[1, 2, 3]
    )
    prompt_id: int = Field(
        ...,
        description="프롬프트 ID입니다.",
        examples=[1, 2, 3]
    )
    version: int = Field(
        ...,
        description="버전 번호입니다.",
        examples=[1, 2, 3]
    )
    content: str = Field(
        ...,
        description="프롬프트 내용입니다.",
        examples=["You are a helpful AI assistant."]
    )
    change_note: Optional[str] = Field(
        None,
        description="변경 사항 메모입니다.",
        examples=["초기 버전", "시간 변수 추가"]
    )
    created_by: str = Field(
        ...,
        description="수정자 ID입니다.",
        examples=["68b3b2e0-0d96-4dd9-a1c3-313d5e388628"]
    )
    created_at: datetime = Field(
        ...,
        description="생성 날짜입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("created_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)


# ===== 계명대 챗봇 아이템 (기능정의서 3. ERD) =====

class FaqCategory(BaseItem):
    """FAQ 카테고리 아이템"""

    id: UUID = Field(
        ...,
        description="카테고리 ID입니다.",
        examples=["3f2b1c44-9a1e-4a5f-8b7c-2d1e0f9a8b7c"]
    )
    parent_id: Optional[UUID] = Field(
        None,
        description="상위 카테고리 ID입니다. 최상위인 경우 NULL입니다."
    )
    category_name: str = Field(
        ...,
        description="카테고리명입니다.",
        examples=["학사", "장학", "취업"]
    )
    category_code: str = Field(
        ...,
        description="카테고리 코드입니다.",
        examples=["ACADEMIC", "SCHOLARSHIP", "CAREER"]
    )
    department_code: Optional[str] = Field(
        None,
        description="담당 부서 코드입니다.",
        examples=["ACAD_AFFAIRS"]
    )
    display_order: int = Field(
        0,
        description="노출 순서입니다.",
        examples=[1, 2, 3]
    )
    is_active: bool = Field(
        True,
        description="사용 여부입니다.",
        examples=[True, False]
    )
    created_at: datetime = Field(
        ...,
        description="생성 일시입니다.",
        examples=[util.get_now()]
    )
    updated_at: datetime = Field(
        ...,
        description="수정 일시입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("created_at", "updated_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

class FaqItem(BaseItem):
    """FAQ 항목 아이템"""

    id: UUID = Field(
        ...,
        description="FAQ ID입니다.",
        examples=["8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f"]
    )
    category_id: UUID = Field(
        ...,
        description="카테고리 ID입니다."
    )
    question: str = Field(
        ...,
        description="질문입니다.",
        examples=["수강신청 기간은 언제인가요?"]
    )
    answer: str = Field(
        ...,
        description="답변입니다.",
        examples=["2026학년도 1학기 수강신청은 2026년 2월 10일부터 2월 14일까지입니다."]
    )
    question_aliases_json: list[str] = Field(
        default_factory=list,
        description="유사 질문 목록입니다. 임베딩 시 질문과 함께 색인됩니다.",
        examples=[["수강신청 언제야", "수강신청 일정 알려줘"]]
    )
    tags_json: list[str] = Field(
        default_factory=list,
        description="태그 목록입니다.",
        examples=[["수강신청", "학사일정"]]
    )
    source_url: Optional[str] = Field(
        None,
        description="원문 URL입니다.",
        examples=["https://www.kmu.ac.kr/notice/1234"]
    )
    department_code: Optional[str] = Field(
        None,
        description="담당 부서 코드입니다.",
        examples=["ACAD_AFFAIRS"]
    )
    visibility: FaqVisibility = Field(
        FaqVisibility.PUBLIC,
        description="공개 범위입니다.",
        examples=list(FaqVisibility)
    )
    status: FaqStatus = Field(
        FaqStatus.DRAFT,
        description="상태입니다.",
        examples=list(FaqStatus)
    )
    language: Language = Field(
        Language.KO,
        description="언어입니다.",
        examples=list(Language)
    )
    version: int = Field(
        1,
        description="버전입니다.",
        examples=[1, 2]
    )
    created_at: datetime = Field(
        ...,
        description="생성 일시입니다.",
        examples=[util.get_now()]
    )
    updated_at: datetime = Field(
        ...,
        description="수정 일시입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("created_at", "updated_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

class FaqEmbedding(BaseItem):
    """FAQ 임베딩 아이템

    `embedding` 벡터 본문은 응답에 담지 않습니다. 차원 수만큼의 실수 배열이라
    목록 조회 응답이 급격히 커지고, 클라이언트가 쓸 일도 없기 때문입니다.
    """

    id: UUID = Field(
        ...,
        description="임베딩 ID입니다."
    )
    faq_id: UUID = Field(
        ...,
        description="FAQ ID입니다."
    )
    embedding_text: str = Field(
        ...,
        description="임베딩에 사용된 텍스트입니다."
    )
    embedding_text_hash: str = Field(
        ...,
        description="임베딩 텍스트의 SHA-256 해시입니다. 원문 변경 감지에 사용됩니다."
    )
    embedding_model: str = Field(
        ...,
        description="임베딩 모델명입니다.",
        examples=["text-embedding-3-small"]
    )
    embedding_version: Optional[str] = Field(
        None,
        description="임베딩 버전입니다.",
        examples=["v1"]
    )
    vector_status: VectorStatus = Field(
        VectorStatus.PENDING,
        description="벡터 색인 상태입니다.",
        examples=list(VectorStatus)
    )
    category_code: Optional[str] = Field(
        None,
        description="카테고리 코드입니다. (검색 필터용 비정규화)",
        examples=["ACADEMIC"]
    )
    department_code: Optional[str] = Field(
        None,
        description="담당 부서 코드입니다. (검색 필터용 비정규화)"
    )
    language: Language = Field(
        Language.KO,
        description="언어입니다. (검색 필터용 비정규화)",
        examples=list(Language)
    )
    visibility: FaqVisibility = Field(
        FaqVisibility.PUBLIC,
        description="공개 범위입니다. (검색 필터용 비정규화)",
        examples=list(FaqVisibility)
    )
    status: FaqStatus = Field(
        FaqStatus.DRAFT,
        description="FAQ 상태입니다. (검색 필터용 비정규화)",
        examples=list(FaqStatus)
    )
    tags: list[str] = Field(
        default_factory=list,
        description="태그 목록입니다.",
        examples=[["수강신청", "학사일정"]]
    )
    source_url: Optional[str] = Field(
        None,
        description="원문 URL입니다."
    )
    question: str = Field(
        ...,
        description="색인 시점의 질문 스냅샷입니다."
    )
    version: int = Field(
        1,
        description="색인 시점의 FAQ 버전입니다.",
        examples=[1, 2]
    )
    indexed_at: Optional[datetime] = Field(
        None,
        description="색인 일시입니다."
    )
    created_at: datetime = Field(
        ...,
        description="생성 일시입니다.",
        examples=[util.get_now()]
    )
    updated_at: datetime = Field(
        ...,
        description="수정 일시입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("indexed_at", "created_at", "updated_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

class ChatSession(BaseItem):
    """대화 세션 아이템"""

    id: UUID = Field(
        ...,
        description="세션 ID입니다."
    )
    user_name: str = Field(
        ...,
        description="사용자명입니다.",
        examples=["20241234"]
    )
    title: Optional[str] = Field(
        None,
        description="세션 제목입니다.",
        examples=["수강신청 문의"]
    )
    language: Language = Field(
        Language.KO,
        description="대화 언어입니다.",
        examples=list(Language)
    )
    status: ChatSessionStatus = Field(
        ChatSessionStatus.ACTIVE,
        description="세션 상태입니다.",
        examples=list(ChatSessionStatus)
    )
    message_count: int = Field(
        0,
        description="메시지 수입니다.",
        examples=[4]
    )
    summary: Optional[str] = Field(
        None,
        description="누적 대화 요약입니다."
    )
    profile: Optional[dict] = Field(
        None,
        description="개인화 컨텍스트 스냅샷입니다.",
        examples=[{"student_no": "20241234", "grade": 3, "major": "컴퓨터공학전공"}]
    )
    last_active_at: datetime = Field(
        ...,
        description="마지막 활동 일시입니다.",
        examples=[util.get_now()]
    )
    created_at: datetime = Field(
        ...,
        description="생성 일시입니다.",
        examples=[util.get_now()]
    )
    updated_at: datetime = Field(
        ...,
        description="수정 일시입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("last_active_at", "created_at", "updated_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

class ChatMessage(BaseItem):
    """대화 메시지 아이템"""

    id: UUID = Field(
        ...,
        description="메시지 ID입니다."
    )
    session_id: UUID = Field(
        ...,
        description="세션 ID입니다."
    )
    role: ChatRole = Field(
        ...,
        description="메시지 역할입니다.",
        examples=list(ChatRole)
    )
    content: str = Field(
        ...,
        description="메시지 내용입니다."
    )
    detected_intent: Optional[ChatIntent] = Field(
        None,
        description="감지 의도입니다.",
        examples=list(ChatIntent)
    )
    sources: Optional[list[dict]] = Field(
        None,
        description="응답 근거 목록입니다.",
        examples=[[{"source_type": "faq", "source_id": "8c1d2e3f-4a5b-6c7d-8e9f-0a1b2c3d4e5f", "score": 0.91}]]
    )
    attachments: Optional[list[dict]] = Field(
        None,
        description="첨부 파일 목록입니다.",
        examples=[[{"file_name": "시간표.png", "file_type": "image/png"}]]
    )
    model_name: Optional[str] = Field(
        None,
        description="응답 생성에 사용한 모델명입니다."
    )
    latency_ms: int = Field(
        0,
        description="응답 생성 시간(ms)입니다.",
        examples=[1350]
    )
    is_answered: bool = Field(
        True,
        description="응답 성공 여부입니다.",
        examples=[True, False]
    )
    created_at: datetime = Field(
        ...,
        description="생성 일시입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("created_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

class RetrievalLog(BaseItem):
    """검색 로그 아이템"""

    id: UUID = Field(
        ...,
        description="검색 로그 ID입니다."
    )
    session_id: UUID = Field(
        ...,
        description="세션 ID입니다."
    )
    message_id: UUID = Field(
        ...,
        description="메시지 ID입니다."
    )
    query_text: str = Field(
        ...,
        description="사용자 질문입니다."
    )
    detected_intent: Optional[ChatIntent] = Field(
        None,
        description="감지 의도입니다.",
        examples=list(ChatIntent)
    )
    collection_name: Optional[str] = Field(
        None,
        description="검색 컬렉션명입니다.",
        examples=["kmu_faq_knowledge"]
    )
    selected_source_id: Optional[UUID] = Field(
        None,
        description="선택된 FAQ ID입니다."
    )
    result_count: int = Field(
        0,
        description="검색 결과 수입니다.",
        examples=[5]
    )
    latency_ms: int = Field(
        0,
        description="검색 지연 시간(ms)입니다.",
        examples=[120]
    )
    created_at: datetime = Field(
        ...,
        description="생성 일시입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("created_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

class UnansweredQuestion(BaseItem):
    """미응답 질문 아이템"""

    id: UUID = Field(
        ...,
        description="미응답 질문 ID입니다."
    )
    session_id: UUID = Field(
        ...,
        description="세션 ID입니다."
    )
    message_id: UUID = Field(
        ...,
        description="메시지 ID입니다."
    )
    question_text: str = Field(
        ...,
        description="질문 내용입니다."
    )
    reason: UnansweredReason = Field(
        ...,
        description="미응답 사유입니다.",
        examples=list(UnansweredReason)
    )
    review_status: ReviewStatus = Field(
        ReviewStatus.PENDING,
        description="검토 상태입니다.",
        examples=list(ReviewStatus)
    )
    reviewed_by: Optional[str] = Field(
        None,
        description="검토자입니다.",
        examples=["admin"]
    )
    reviewed_at: Optional[datetime] = Field(
        None,
        description="검토 일시입니다."
    )
    created_at: datetime = Field(
        ...,
        description="생성 일시입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("reviewed_at", "created_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

class UserFeedback(BaseItem):
    """사용자 피드백 아이템"""

    id: UUID = Field(
        ...,
        description="피드백 ID입니다."
    )
    session_id: UUID = Field(
        ...,
        description="세션 ID입니다."
    )
    message_id: UUID = Field(
        ...,
        description="메시지 ID입니다."
    )
    rating: int = Field(
        ...,
        description="평점입니다. (1~5)",
        examples=[5]
    )
    feedback_text: Optional[str] = Field(
        None,
        description="피드백 내용입니다.",
        examples=["원하는 답변을 정확히 받았습니다."]
    )
    created_at: datetime = Field(
        ...,
        description="생성 일시입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("created_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

class IngestionJob(BaseItem):
    """수집 작업 아이템"""

    id: UUID = Field(
        ...,
        description="수집 작업 ID입니다."
    )
    source_type: SourceType = Field(
        ...,
        description="원천 유형입니다.",
        examples=list(SourceType)
    )
    status: IngestionStatus = Field(
        IngestionStatus.PENDING,
        description="작업 상태입니다.",
        examples=list(IngestionStatus)
    )
    total_count: int = Field(
        0,
        description="전체 항목 수입니다.",
        examples=[120]
    )
    success_count: int = Field(
        0,
        description="성공 항목 수입니다.",
        examples=[118]
    )
    failed_count: int = Field(
        0,
        description="실패 항목 수입니다.",
        examples=[2]
    )
    error_message: Optional[str] = Field(
        None,
        description="오류 메시지입니다."
    )
    started_at: datetime = Field(
        ...,
        description="시작 일시입니다.",
        examples=[util.get_now()]
    )
    ended_at: Optional[datetime] = Field(
        None,
        description="종료 일시입니다."
    )


    @field_serializer("started_at", "ended_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)

class IngestionJobItem(BaseItem):
    """수집 작업 항목 아이템"""

    id: UUID = Field(
        ...,
        description="수집 작업 항목 ID입니다."
    )
    job_id: UUID = Field(
        ...,
        description="수집 작업 ID입니다."
    )
    source_table: str = Field(
        ...,
        description="원천 테이블명입니다.",
        examples=["faq_items"]
    )
    source_id: UUID = Field(
        ...,
        description="원천 데이터 ID입니다."
    )
    status: IngestionItemStatus = Field(
        IngestionItemStatus.PENDING,
        description="처리 상태입니다.",
        examples=list(IngestionItemStatus)
    )
    error_message: Optional[str] = Field(
        None,
        description="오류 메시지입니다."
    )
    created_at: datetime = Field(
        ...,
        description="생성 일시입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("created_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)


class MaskingRule(BaseItem):
    """개인정보 마스킹 규칙 아이템"""

    id: UUID = Field(
        ...,
        description="규칙 ID입니다."
    )
    name: str = Field(
        ...,
        description="규칙명입니다.",
        examples=["전화번호"]
    )
    target_field: MaskingTargetField = Field(
        ...,
        description="대상 필드 분류입니다.",
        examples=list(MaskingTargetField)
    )
    regex_pattern: str = Field(
        ...,
        description="정규표현식입니다.",
        examples=[r"01[0-9]-?\d{3,4}-?\d{4}"]
    )
    masking_method: MaskingMethod = Field(
        ...,
        description="마스킹 방식입니다.",
        examples=list(MaskingMethod)
    )
    replacement: str = Field(
        "****",
        description="치환 문자열입니다.",
        examples=["****"]
    )
    description: Optional[str] = Field(
        None,
        description="설명입니다."
    )
    is_active: bool = Field(
        True,
        description="활성 여부입니다.",
        examples=[True, False]
    )
    created_at: datetime = Field(
        ...,
        description="생성 일시입니다.",
        examples=[util.get_now()]
    )
    updated_at: datetime = Field(
        ...,
        description="수정 일시입니다.",
        examples=[util.get_now()]
    )


    @field_serializer("created_at", "updated_at")
    def serialize_datetime(value: datetime) -> Optional[str]:
        return util.serialize_datetime(value)
