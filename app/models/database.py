import orjson
import uuid

from shapely import wkb

from peewee import (
    Field as PeeweeField,
    Model,
    CharField,
    TextField,
    IntegerField,
    FloatField,
    AutoField,
    BooleanField,
    UUIDField,
    DatabaseProxy,
)

from psycopg2.extensions import AsIs

from playhouse.postgres_ext import DateTimeTZField, JSONField, BinaryJSONField

from pgvector.peewee import VectorField

from app.config import config
import app.utils.common as util


database_proxy = DatabaseProxy()

FAQ_EMBEDDING_DIM = config.chatbot.embedding_dim
"""FAQ 임베딩 벡터 차원 (configs/config.yaml의 chatbot.embedding_dim)

HNSW 인덱스는 고정 차원 컬럼에만 만들 수 있어 설정값으로 컬럼 타입을 고정합니다.
`chatbot.embedding_model`이 실제로 내놓는 차원과 반드시 일치해야 하며,
값을 바꾸면 컬럼과 인덱스를 재생성하고 전량 재색인해야 합니다.
"""


class BaseModel(Model):
    class Meta:
        database = database_proxy


# ===== Fields =====

class EnumField(CharField):
    def __init__(self, choices, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.choices = choices
        
    def db_value(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            if value not in self.choices:
                raise ValueError(f"'{value}'는 유효한 옵션이 아닙니다. 유효한 옵션: {self.choices}")
            return value
        return str(value)
    
    def python_value(self, value):
        if value is None:
            return None
        return value

class EnumIntField(IntegerField):
    def __init__(self, choices, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.choices = choices
        
    def db_value(self, value):
        if value is None:
            return None
        if isinstance(value, int):
            if value not in self.choices:
                raise ValueError(f"'{value}'는 유효한 옵션이 아닙니다. 유효한 옵션: {self.choices}")
            return value
        return int(value)
    
    def python_value(self, value):
        if value is None:
            return None
        return value

class CustomJSONField(JSONField):
    def __init__(self, dumps=None, *args, **kwargs):
        self.dumps = dumps or orjson.dumps
        super(CustomJSONField, self).__init__(*args, **kwargs)

class CustomBinaryJSONField(BinaryJSONField):
    """JSONB 컬럼용 필드.

    기능정의서 ERD가 `JSONB`로 명시한 컬럼(태그·유사 질문 목록 등)에 사용합니다.
    JSONB는 GIN 인덱스로 태그/키워드 필터가 가능해 관리자 통계(KAI-REQ-031)에서 유리합니다.
    """

    def __init__(self, dumps=None, *args, **kwargs):
        self.dumps = dumps or orjson.dumps
        super(CustomBinaryJSONField, self).__init__(*args, **kwargs)

class Point5181:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __iter__(self):
        yield self.x
        yield self.y

    def __repr__(self):
        return f"Point5181(x={self.x}, y={self.y})"

class Geometry5181Field(PeeweeField):
    field_type = "geometry(Point,5181)"

    def db_value(self, value):
        import app.models.db_item as db_items
        
        if value is None:
            return None
        if isinstance(value, Point5181) or isinstance(value, db_items.Point5181):
            return AsIs(
                "ST_SetSRID(ST_MakePoint({}, {}), 5181)".format(value.x, value.y)
            )
        if isinstance(value, dict) and 'x' in value and 'y' in value:
            return AsIs(
                "ST_SetSRID(ST_MakePoint({}, {}), 5181)".format(value['x'], value['y'])
            )
        if hasattr(value, 'x') and hasattr(value, 'y'):
            return AsIs(
                "ST_SetSRID(ST_MakePoint({}, {}), 5181)".format(value.x, value.y)
            )
        if isinstance(value, tuple) and len(value) >= 2:
            return AsIs(
                "ST_SetSRID(ST_MakePoint({}, {}), 5181)".format(value[0], value[1])
            )
        return value

    def python_value(self, value):
        if value is None:
            return None
        if isinstance(value, memoryview):
            value = bytes(value)
        if isinstance(value, (bytes, bytearray, str)):
            if isinstance(value, bytearray):
                value = bytes(value)
            point = wkb.loads(value)
            return Point5181(point.coords[0][0], point.coords[0][1])
        if isinstance(value, tuple):
            return Point5181(*value)
        return value


# ===== Tables =====

class Model(BaseModel):
    """모델 정보 테이블"""
    
    from app.models.enum import ModelProvider, ModelType, ModelStatus

    name = CharField(max_length=255, primary_key=True)
    """모델명 (사용자 정의)"""
    provider = EnumField(choices=list(ModelProvider), index=True)
    """모델 제공자"""
    model_id = CharField(max_length=255)
    """모델 ID (제공자의 모델 ID)"""
    model_type = EnumField(choices=list(ModelType), index=True)
    """모델 타입"""
    api_base = CharField(max_length=512, null=True)
    """API Base URL (Local, OpenAI Compatible용)"""
    api_key = CharField(max_length=512, null=True)
    """API Key"""
    description = CharField(max_length=1024, null=True)
    """모델 설명"""
    cost = CustomJSONField(null=True)
    """모델 비용"""
    status = EnumField(choices=list(ModelStatus), default=ModelStatus.STOPPED, index=True)
    """모델 상태"""
    error_message = CharField(max_length=1024, null=True)
    """오류 메시지"""
    config = CustomJSONField(default=dict)
    """모델 설정 (JSON)"""
    created_at = DateTimeTZField(default=util.get_now)
    """등록 날짜"""
    updated_at = DateTimeTZField(default=util.get_now)
    """수정 날짜"""
    
    class Meta:
        table_name = "models"


class ModelUsage(BaseModel):
    """모델 사용량 테이블"""
    
    from app.models.enum import ModelType, ModelUsageStatus

    id = AutoField(primary_key=True)
    """사용량 ID"""
    user_name = CharField(max_length=255, index=True)
    """사용자명"""
    model_name = CharField(max_length=255, index=True)
    """모델명"""
    model_type = EnumField(choices=list(ModelType), index=True)
    """모델 타입"""
    usage = CustomJSONField(null=True)
    """모델 사용량"""
    request_id = CharField(max_length=255, null=True)
    """요청 ID"""
    latency_ms = IntegerField(default=0)
    """응답 시간 (ms)"""
    status = EnumField(choices=list(ModelUsageStatus), default=ModelUsageStatus.SUCCESS)
    """요청 상태"""
    error_message = CharField(max_length=1024, null=True)
    """오류 메시지"""
    metadata = CustomJSONField(null=True)
    """추가 메타데이터 (JSON)"""
    created_at = DateTimeTZField(default=util.get_now, index=True)
    """생성 날짜"""
    
    class Meta:
        table_name = "model_usages"


class Collection(BaseModel):
    """컬렉션 정보 테이블"""
    
    from app.models.enum import GraphIndexStatus
    
    name = CharField(max_length=255, primary_key=True)
    """컬렉션 이름"""
    user_name = CharField(max_length=255, index=True)
    """사용자명"""
    embedding_model = CharField(max_length=255)
    """임베딩에 사용된 모델명"""
    vector_size = IntegerField()
    """벡터 차원 크기"""
    description = CharField(max_length=1024, null=True)
    """컬렉션 설명"""
    access_scope = CharField(max_length=512, null=True, index=True)
    """접근 가능 범위: NULL=전체, group:{group_name}=그룹, user:{user_name}=사용자"""
    is_system = BooleanField(default=False, index=True)
    """시스템 전용 컬렉션 여부: True인 경우 기본적으로 목록에 표시되지 않음"""
    is_active = BooleanField(default=True, index=True)
    """검색(RAG) 활성 여부: False면 챗봇 검색 대상에서 제외할 수 있음"""
    chunk_size = IntegerField(default=1000)
    """문서 청킹 목표 크기(문자 수)"""
    chunk_overlap = IntegerField(default=100)
    """청크 간 오버랩 크기(문자 수)"""
    top_k = IntegerField(default=5)
    """검색 시 반환할 상위 결과 수"""
    similarity_threshold = FloatField(default=0.35)
    """검색 최소 유사도 점수 (미달 시 제외)"""
    graph_index_status = EnumField(choices=list(GraphIndexStatus), default=GraphIndexStatus.NO_INDEXED, index=True)
    """그래프 인덱싱 상태"""
    graph_index_error = CharField(max_length=1024, null=True)
    """그래프 인덱싱 오류 메시지"""
    graph_indexed_at = DateTimeTZField(null=True)
    """마지막 그래프 인덱싱 날짜"""
    created_at = DateTimeTZField(default=util.get_now, index=True)
    """생성 날짜"""
    updated_at = DateTimeTZField(default=util.get_now)
    """수정 날짜"""
    
    class Meta:
        table_name = "collections"


class Document(BaseModel):
    """문서 정보 테이블"""
    
    from app.models.enum import DocumentStatus
    
    id = AutoField(primary_key=True)
    """문서 ID"""
    collection_name = CharField(max_length=255, index=True)
    """컬렉션 이름"""
    user_name = CharField(max_length=255, index=True)
    """사용자명"""
    file_name = CharField(max_length=512)
    """파일명"""
    file_path = CharField(max_length=1024)
    """S3 파일 경로"""
    file_size = IntegerField()
    """파일 크기 (bytes)"""
    file_type = CharField(max_length=50)
    """파일 타입 (pdf, docx, txt 등)"""
    chunk_count = IntegerField(default=0)
    """청크 개수"""
    metadata = CustomJSONField(null=True)
    """문서 메타데이터 (제목, 작성자, 생성일, 수정일 등)"""
    status = EnumField(choices=list(DocumentStatus), default=DocumentStatus.PROCESSING, index=True)
    """문서 처리 상태"""
    error_message = CharField(max_length=1024, null=True)
    """오류 메시지"""
    created_at = DateTimeTZField(default=util.get_now, index=True)
    """등록 날짜"""
    updated_at = DateTimeTZField(default=util.get_now)
    """수정 날짜"""
    
    class Meta:
        table_name = "documents"


class BaseDocumentChunk(BaseModel):
    """문서 청크 공통 스키마 (추상 베이스)

    실제 저장은 차원별 구체 테이블(`DocumentChunk384` 등)이 담당합니다.
    이 클래스는 `TABLES`에 넣지 않으므로 테이블이 생성되지 않습니다.

    검색 필터로 자주 쓰이는 항목(문서 ID·청크 순서·페이지·파일명)은 컬럼으로 승격하고,
    나머지 파서 메타데이터는 `metadata` JSONB에 담습니다.
    """

    id = UUIDField(primary_key=True, default=uuid.uuid4)
    """청크 ID"""
    collection_name = CharField(max_length=255, index=True)
    """컬렉션 이름 (collections.name)"""
    document_id = IntegerField(index=True)
    """문서 ID (documents.id)"""
    chunk_index = IntegerField()
    """문서 내 청크 순번"""
    content = TextField()
    """청크 본문"""
    page = IntegerField(null=True)
    """원본 페이지 번호"""
    file_name = CharField(max_length=512, null=True)
    """원본 파일명"""
    metadata = CustomBinaryJSONField(null=True)
    """청크 메타데이터 (컬럼으로 승격되지 않은 나머지 payload)"""
    created_at = DateTimeTZField(default=util.get_now, index=True)
    """생성 날짜"""


class DocumentChunk384(BaseDocumentChunk):
    """384차원 문서 청크 테이블 (예: multilingual-e5-small)"""

    embedding = VectorField(dimensions=384)
    """임베딩 벡터"""

    class Meta:
        table_name = "document_chunks_384"


class DocumentChunk768(BaseDocumentChunk):
    """768차원 문서 청크 테이블 (예: ko-sroberta-multitask, multilingual-e5-base)"""

    embedding = VectorField(dimensions=768)
    """임베딩 벡터"""

    class Meta:
        table_name = "document_chunks_768"


class DocumentChunk1024(BaseDocumentChunk):
    """1024차원 문서 청크 테이블 (예: bge-m3, multilingual-e5-large)"""

    embedding = VectorField(dimensions=1024)
    """임베딩 벡터"""

    class Meta:
        table_name = "document_chunks_1024"


class DocumentChunk1536(BaseDocumentChunk):
    """1536차원 문서 청크 테이블 (예: text-embedding-3-small)"""

    embedding = VectorField(dimensions=1536)
    """임베딩 벡터"""

    class Meta:
        table_name = "document_chunks_1536"


class DocumentChunk3072(BaseDocumentChunk):
    """3072차원 문서 청크 테이블 (예: text-embedding-3-large)"""

    embedding = VectorField(dimensions=3072)
    """임베딩 벡터"""

    class Meta:
        table_name = "document_chunks_3072"


DOCUMENT_CHUNK_MODELS: dict[int, type[BaseDocumentChunk]] = {
    384: DocumentChunk384,
    768: DocumentChunk768,
    1024: DocumentChunk1024,
    1536: DocumentChunk1536,
    3072: DocumentChunk3072,
}
"""벡터 차원 -> 문서 청크 테이블 매핑

pgvector의 HNSW 인덱스는 차원이 고정된 컬럼에만 걸 수 있는데, 컬렉션마다 임베딩 모델이
달라 차원도 달라집니다. 컬렉션마다 런타임에 테이블을 만들면(동적 DDL) SQL 인젝션 표면과
마이그레이션 복잡도가 커지므로, 실사용 모델을 사실상 모두 덮는 차원별 정적 테이블을 두고
컬렉션은 `collection_name` 컬럼으로 구분합니다.

여기에 없는 차원의 모델은 컬렉션 생성 단계에서 거부됩니다.
"""


class Prompt(BaseModel):
    """프롬프트"""
    
    id = AutoField()
    """프롬프트 ID"""
    name = CharField(max_length=255, unique=True, index=True)
    """프롬프트 이름 (고유)"""
    description = CharField(max_length=1024, null=True)
    """프롬프트 설명"""
    content = CharField(max_length=65535)
    """프롬프트 내용"""
    current_version = IntegerField(default=1)
    """현재 버전"""
    tags = CustomJSONField(null=True)
    """태그 배열"""
    access_scope = CharField(max_length=512, null=True, index=True)
    """접근 가능 범위: NULL=전체, group:{group_name}=그룹, user:{user_name}=사용자"""
    is_system = BooleanField(default=False, index=True)
    """시스템 전용 프롬프트 여부: True인 경우 기본적으로 목록에 표시되지 않음"""
    created_by = CharField(max_length=255)
    """생성자 ID"""
    created_at = DateTimeTZField(default=util.get_now, index=True)
    """생성 날짜"""
    updated_at = DateTimeTZField(default=util.get_now)
    """수정 날짜"""
    
    class Meta:
        table_name = "prompts"


class PromptVersion(BaseModel):
    """프롬프트 버전"""
    
    id = AutoField()
    """버전 ID"""
    prompt_id = IntegerField(index=True)
    """프롬프트 ID"""
    version = IntegerField()
    """버전 번호"""
    content = CharField(max_length=65535)
    """프롬프트 내용"""
    change_note = CharField(max_length=1024, null=True)
    """변경 사항 메모"""
    created_by = CharField(max_length=255)
    """수정자 ID"""
    created_at = DateTimeTZField(default=util.get_now, index=True)
    """생성 날짜"""
    
    class Meta:
        table_name = "prompt_versions"
        indexes = (
            (('prompt_id', 'version'), True),
        )


# ===== 계명대 챗봇 테이블 (기능정의서 3. ERD) =====

class FaqCategory(BaseModel):
    """FAQ 카테고리 테이블"""

    id = UUIDField(primary_key=True, default=uuid.uuid4)
    """카테고리 ID"""
    parent_id = UUIDField(null=True, index=True)
    """상위 카테고리 ID"""
    category_name = CharField(max_length=100)
    """카테고리명"""
    category_code = CharField(max_length=50, unique=True, index=True)
    """카테고리 코드"""
    department_code = CharField(max_length=50, null=True, index=True)
    """담당 부서 코드"""
    display_order = IntegerField(default=0)
    """노출 순서"""
    is_active = BooleanField(default=True, index=True)
    """사용 여부"""
    created_at = DateTimeTZField(default=util.get_now)
    """생성 일시"""
    updated_at = DateTimeTZField(default=util.get_now)
    """수정 일시"""

    class Meta:
        table_name = "faq_categories"


class FaqItem(BaseModel):
    """FAQ 항목 테이블"""

    from app.models.enum import FaqVisibility, FaqStatus, Language

    id = UUIDField(primary_key=True, default=uuid.uuid4)
    """FAQ ID"""
    category_id = UUIDField(index=True)
    """카테고리 ID (faq_categories.id)"""
    question = TextField()
    """질문"""
    answer = TextField()
    """답변"""
    question_aliases_json = CustomBinaryJSONField(default=list)
    """유사 질문 목록"""
    tags_json = CustomBinaryJSONField(default=list)
    """태그 목록"""
    source_url = TextField(null=True)
    """원문 URL"""
    department_code = CharField(max_length=50, null=True, index=True)
    """담당 부서 코드"""
    visibility = EnumField(choices=list(FaqVisibility), default=FaqVisibility.PUBLIC, index=True)
    """공개 범위"""
    status = EnumField(choices=list(FaqStatus), default=FaqStatus.DRAFT, index=True)
    """상태"""
    language = EnumField(choices=list(Language), default=Language.KO, index=True)
    """언어"""
    version = IntegerField(default=1)
    """버전"""
    created_at = DateTimeTZField(default=util.get_now, index=True)
    """생성 일시"""
    updated_at = DateTimeTZField(default=util.get_now)
    """수정 일시"""

    class Meta:
        table_name = "faq_items"


class FaqEmbedding(BaseModel):
    """FAQ 임베딩 테이블 (pgvector)

    기능정의서 ERD는 이 자리에 Qdrant 컬렉션(`kmu_faq_knowledge`)과 그 매핑 테이블
    `faq_embedding_index`를 두었으나, 벡터 저장소를 PostgreSQL pgvector + HNSW로
    대체하면서 벡터와 매핑을 한 테이블로 합쳤습니다. 별도 벡터 DB를 띄우지 않아도 되고,
    FAQ 원문과 벡터가 같은 트랜잭션에서 갱신되어 정합성이 깨질 여지가 없습니다.

    ERD의 `qdrant.collection` payload에 해당하던 필터 항목(카테고리·부서·언어·공개범위·상태)은
    검색 시 벡터 스캔 전에 걸러내기 위해 이 테이블에 비정규화해 둡니다.

    `embedding_text_hash`가 현재 질문의 해시와 다르면 원문이 바뀐 것이므로
    재색인 대상(`VectorStatus.STALE`)으로 판정합니다.
    """

    from app.models.enum import VectorStatus, FaqStatus, FaqVisibility, Language

    id = UUIDField(primary_key=True, default=uuid.uuid4)
    """임베딩 ID"""
    faq_id = UUIDField(unique=True, index=True)
    """FAQ ID (faq_items.id)"""
    embedding = VectorField(dimensions=FAQ_EMBEDDING_DIM, null=True)
    """임베딩 벡터 (색인 실패 시 NULL)"""
    embedding_text = TextField()
    """임베딩 텍스트 (질문 + 유사 질문)"""
    embedding_text_hash = CharField(max_length=64, index=True)
    """임베딩 텍스트 해시 (SHA-256)"""
    embedding_model = CharField(max_length=100)
    """임베딩 모델명"""
    embedding_version = CharField(max_length=30, null=True)
    """임베딩 버전"""
    vector_status = EnumField(choices=list(VectorStatus), default=VectorStatus.PENDING, index=True)
    """벡터 색인 상태"""
    category_code = CharField(max_length=50, null=True, index=True)
    """카테고리 코드 (검색 필터용 비정규화)"""
    department_code = CharField(max_length=50, null=True, index=True)
    """담당 부서 코드 (검색 필터용 비정규화)"""
    language = EnumField(choices=list(Language), default=Language.KO, index=True)
    """언어 (검색 필터용 비정규화)"""
    visibility = EnumField(choices=list(FaqVisibility), default=FaqVisibility.PUBLIC, index=True)
    """공개 범위 (검색 필터용 비정규화)"""
    status = EnumField(choices=list(FaqStatus), default=FaqStatus.DRAFT, index=True)
    """FAQ 상태 (검색 필터용 비정규화)"""
    tags = CustomBinaryJSONField(default=list)
    """태그 목록 (검색 필터용 비정규화)"""
    source_url = TextField(null=True)
    """원문 URL (검색 결과 출처 표기용)"""
    question = TextField()
    """질문 스냅샷 (검색 결과 표기용, 답변 본문은 조회 시 faq_items에서 최신본을 읽음)"""
    version = IntegerField(default=1)
    """색인 시점의 FAQ 버전"""
    indexed_at = DateTimeTZField(null=True)
    """색인 일시"""
    created_at = DateTimeTZField(default=util.get_now)
    """생성 일시"""
    updated_at = DateTimeTZField(default=util.get_now)
    """수정 일시"""

    class Meta:
        table_name = "faq_embeddings"


class ChatSession(BaseModel):
    """대화 세션 테이블

    ERD의 `session_id`/`message_id`가 가리키는 실체이며, 재접속 시 이전 맥락을
    복원하는 세션 기억(KAI-REQ-041)의 저장소입니다.
    """

    from app.models.enum import ChatSessionStatus, Language

    id = UUIDField(primary_key=True, default=uuid.uuid4)
    """세션 ID"""
    user_name = CharField(max_length=255, index=True)
    """사용자명 (SSO 계정)"""
    title = CharField(max_length=255, null=True)
    """세션 제목 (첫 질문 기반 자동 생성)"""
    language = EnumField(choices=list(Language), default=Language.KO)
    """대화 언어"""
    status = EnumField(choices=list(ChatSessionStatus), default=ChatSessionStatus.ACTIVE, index=True)
    """세션 상태"""
    message_count = IntegerField(default=0)
    """메시지 수"""
    summary = TextField(null=True)
    """누적 대화 요약 (문맥 유지용)"""
    profile = CustomBinaryJSONField(null=True)
    """개인화 컨텍스트 (학번·학년·전공 등 스냅샷)"""
    last_active_at = DateTimeTZField(default=util.get_now, index=True)
    """마지막 활동 일시"""
    created_at = DateTimeTZField(default=util.get_now, index=True)
    """생성 일시"""
    updated_at = DateTimeTZField(default=util.get_now)
    """수정 일시"""

    class Meta:
        table_name = "chat_sessions"


class ChatMessage(BaseModel):
    """대화 메시지 테이블"""

    from app.models.enum import ChatRole, ChatIntent

    id = UUIDField(primary_key=True, default=uuid.uuid4)
    """메시지 ID"""
    session_id = UUIDField(index=True)
    """세션 ID (chat_sessions.id)"""
    role = EnumField(choices=list(ChatRole), index=True)
    """메시지 역할"""
    content = TextField()
    """메시지 내용"""
    detected_intent = EnumField(choices=list(ChatIntent), null=True, index=True)
    """감지 의도"""
    sources = CustomBinaryJSONField(null=True)
    """응답 근거 목록 (FAQ/문서 출처)"""
    attachments = CustomBinaryJSONField(null=True)
    """첨부 파일 목록 (멀티모달 질의)"""
    model_name = CharField(max_length=255, null=True)
    """응답 생성에 사용한 모델명"""
    latency_ms = IntegerField(default=0)
    """응답 생성 시간(ms)"""
    is_answered = BooleanField(default=True, index=True)
    """응답 성공 여부"""
    created_at = DateTimeTZField(default=util.get_now, index=True)
    """생성 일시"""

    class Meta:
        table_name = "chat_messages"


class RetrievalLog(BaseModel):
    """검색 로그 테이블 (KAI-REQ-043)"""

    from app.models.enum import ChatIntent

    id = UUIDField(primary_key=True, default=uuid.uuid4)
    """검색 로그 ID"""
    session_id = UUIDField(index=True)
    """세션 ID"""
    message_id = UUIDField(index=True)
    """메시지 ID"""
    query_text = TextField()
    """사용자 질문"""
    detected_intent = EnumField(choices=list(ChatIntent), null=True, index=True)
    """감지 의도"""
    collection_name = CharField(max_length=100, null=True)
    """검색 컬렉션명"""
    selected_source_id = UUIDField(null=True, index=True)
    """선택된 FAQ ID"""
    result_count = IntegerField(default=0)
    """검색 결과 수"""
    latency_ms = IntegerField(default=0)
    """검색 지연 시간(ms)"""
    created_at = DateTimeTZField(default=util.get_now, index=True)
    """생성 일시"""

    class Meta:
        table_name = "retrieval_logs"


class UnansweredQuestion(BaseModel):
    """미응답 질문 테이블 (KAI-REQ-031/040)"""

    from app.models.enum import UnansweredReason, ReviewStatus

    id = UUIDField(primary_key=True, default=uuid.uuid4)
    """미응답 질문 ID"""
    session_id = UUIDField(index=True)
    """세션 ID"""
    message_id = UUIDField(index=True)
    """메시지 ID"""
    question_text = TextField()
    """질문 내용"""
    reason = EnumField(choices=list(UnansweredReason), index=True)
    """미응답 사유"""
    review_status = EnumField(choices=list(ReviewStatus), default=ReviewStatus.PENDING, index=True)
    """검토 상태"""
    reviewed_by = CharField(max_length=100, null=True)
    """검토자"""
    reviewed_at = DateTimeTZField(null=True)
    """검토 일시"""
    created_at = DateTimeTZField(default=util.get_now, index=True)
    """생성 일시"""

    class Meta:
        table_name = "unanswered_questions"


class UserFeedback(BaseModel):
    """사용자 피드백 테이블 (KAI-REQ-033 만족도 측정)"""

    id = UUIDField(primary_key=True, default=uuid.uuid4)
    """피드백 ID"""
    session_id = UUIDField(index=True)
    """세션 ID"""
    message_id = UUIDField(index=True)
    """메시지 ID"""
    rating = IntegerField(index=True)
    """평점 (1~5)"""
    feedback_text = TextField(null=True)
    """피드백 내용"""
    created_at = DateTimeTZField(default=util.get_now, index=True)
    """생성 일시"""

    class Meta:
        table_name = "user_feedbacks"
        indexes = (
            (("message_id",), True),
        )


class IngestionJob(BaseModel):
    """수집 작업 테이블 (KAI-REQ-014)"""

    from app.models.enum import SourceType, IngestionStatus

    id = UUIDField(primary_key=True, default=uuid.uuid4)
    """수집 작업 ID"""
    source_type = EnumField(choices=list(SourceType), index=True)
    """원천 유형"""
    status = EnumField(choices=list(IngestionStatus), default=IngestionStatus.PENDING, index=True)
    """작업 상태"""
    total_count = IntegerField(default=0)
    """전체 항목 수"""
    success_count = IntegerField(default=0)
    """성공 항목 수"""
    failed_count = IntegerField(default=0)
    """실패 항목 수"""
    error_message = CharField(max_length=1024, null=True)
    """오류 메시지"""
    started_at = DateTimeTZField(default=util.get_now, index=True)
    """시작 일시"""
    ended_at = DateTimeTZField(null=True)
    """종료 일시"""

    class Meta:
        table_name = "ingestion_jobs"


class IngestionJobItem(BaseModel):
    """수집 작업 항목 테이블"""

    from app.models.enum import IngestionItemStatus

    id = UUIDField(primary_key=True, default=uuid.uuid4)
    """수집 작업 항목 ID"""
    job_id = UUIDField(index=True)
    """수집 작업 ID (ingestion_jobs.id)"""
    source_table = CharField(max_length=100)
    """원천 테이블명"""
    source_id = UUIDField(index=True)
    """원천 데이터 ID"""
    status = EnumField(choices=list(IngestionItemStatus), default=IngestionItemStatus.PENDING, index=True)
    """처리 상태"""
    error_message = TextField(null=True)
    """오류 메시지"""
    created_at = DateTimeTZField(default=util.get_now, index=True)
    """생성 일시"""

    class Meta:
        table_name = "ingestion_job_items"


class MaskingRule(BaseModel):
    """개인정보 마스킹 규칙 테이블"""

    from app.models.enum import MaskingTargetField, MaskingMethod

    id = UUIDField(primary_key=True, default=uuid.uuid4)
    """규칙 ID"""
    name = CharField(max_length=255, index=True)
    """규칙명"""
    target_field = EnumField(choices=list(MaskingTargetField), index=True)
    """대상 필드 분류"""
    regex_pattern = TextField()
    """정규표현식"""
    masking_method = EnumField(choices=list(MaskingMethod), index=True)
    """마스킹 방식"""
    replacement = CharField(max_length=64, default="****")
    """치환 문자열"""
    description = TextField(null=True)
    """설명"""
    is_active = BooleanField(default=True, index=True)
    """활성 여부"""
    created_at = DateTimeTZField(default=util.get_now, index=True)
    """생성 일시"""
    updated_at = DateTimeTZField(default=util.get_now)
    """수정 일시"""

    class Meta:
        table_name = "masking_rules"


TABLES = [
    Model,
    ModelUsage,
    Collection,
    Document,
    DocumentChunk384,
    DocumentChunk768,
    DocumentChunk1024,
    DocumentChunk1536,
    DocumentChunk3072,
    Prompt,
    PromptVersion,
    FaqCategory,
    FaqItem,
    FaqEmbedding,
    ChatSession,
    ChatMessage,
    RetrievalLog,
    UnansweredQuestion,
    UserFeedback,
    IngestionJob,
    IngestionJobItem,
    MaskingRule,
]
