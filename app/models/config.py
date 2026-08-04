from pydantic_settings import BaseSettings

from typing import Literal, Optional

from pydantic import BaseModel


class EnvConfig(BaseSettings):
    """애플리케이션 환경 설정을 관리하는 클래스"""
    
    #region CORS 설정
    CORS_ORIGINS: list[str] = ["*"]
    CORS_HEADERS: list[str] = ["*"]
    CORS_METHODS: list[str] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    #endregion
    
    #region 시스템 설정
    # 시스템 시간대
    TZ: str = "Asia/Seoul"
    # 서버 호스트
    HOST: str = "0.0.0.0"
    # 서버 포트
    PORT: int = 3000
    # 로그 레벨
    LOG_LEVEL: str = "info"
    # 로그 파일 백업 개수(일)
    LOG_BACKUP_COUNT: int = 30
    #endregion
    
    #region API App 설정
    # App 환경
    APP_ENV: Literal["development", "production"] = "development"
    # App 명칭
    APP_TITLE: str = "Keimyung University AI (API)"
    # App 설명
    APP_DESCRIPTION: str = '''
<img src="static/logo.png" alt="주식회사 제이디원 로고" height="80">

계명대학교 AI 챗봇 백엔드입니다.
모든 응답은 항상 JSON 형식으로 반환됩니다.
'''
    # App 루트 경로
    APP_ROOT_PATH: str = "/"
    # OpenAPI (API 문서 JSON) 활성화 여부
    ENABLE_OPENAPI: bool = True
    # Scalar UI (API 문서 UI) 활성화 여부
    ENABLE_SCALAR: bool = True
    # Swagger UI (API 문서 UI) 활성화 여부
    ENABLE_SWAGGER: bool = True
    # ReDoc UI (API 문서 UI) 활성화 여부
    ENABLE_REDOC: bool = True
    # Response 압축 활성화 여부
    ENABLE_COMPRESS: bool = False
    #endregion

    #region 챗봇 설정
    # 미입력 세션 자동 종료 사용 여부 (KAI-REQ-039)
    # false면 유휴 시간과 무관하게 세션이 계속 살아있습니다.
    # (메시지 전송 시점 판정과 5분 주기 정리 잡이 모두 비활성화됩니다)
    ENABLE_SESSION_IDLE_TIMEOUT: bool = True
    # 미입력 자동 종료 기준 시간(분)
    # 0 이하면 ENABLE_SESSION_IDLE_TIMEOUT과 무관하게 자동 종료가 동작하지 않습니다.
    SESSION_IDLE_TIMEOUT_MINUTES: int = 30
    #endregion

    #region 모델 호출 기본값
    # LOCAL/OPENAI_COMPATIBLE 모델 호출 시, 요청에 chat_template_kwargs.enable_thinking이
    # 명시되지 않은 경우 적용할 기본값입니다. None이면 기본값을 주입하지 않습니다(모델/템플릿 기본 동작 유지).
    DEFAULT_ENABLE_THINKING: Optional[bool] = None
    #endregion


class DatabaseConfig(BaseModel):
    """데이터베이스 관련 설정 클래스"""
    
    db_type: Literal["postgresql"]
    """데이터베이스 타입"""
    host: str
    """서버 IP"""
    port: int
    """포트 번호"""
    username: str
    """사용자명"""
    password: str
    """사용자 비밀번호"""
    name: str
    """데이터베이스 이름"""
    pool_min_connections: int
    """Connection Pool 최소 연결 수"""
    pool_max_connections: int
    """Connection Pool 최대 연결 수"""
    extensions: list[str]
    """PostgreSQL 확장 목록"""


class RedisConfig(BaseModel):
    """Redis 관련 설정 클래스"""
    
    host: str
    """서버 IP"""
    port: int
    """포트 번호"""


class AuthConfig(BaseModel):
    """사용자 인증 관련 설정 클래스"""
    
    admin_roles: list[str]
    """관리자 역할 목록"""
    whitelist_domains: list[str]
    """인증 패스 도메인 목록"""
    servers: list["AuthServerConfig"]
    """인증 서버 목록"""


class AuthServerConfig(BaseModel):
    """사용자 인증 서버 관련 설정 클래스"""
    
    alias: str
    """인증 서버 별칭"""
    server_url: str
    """인증 서버 주소"""
    realm_name: str
    """Realm 이름"""
    client_id: str
    """Client ID"""
    client_secret_key: str
    """Client Secret Key"""
    admin_username: str
    """관리자 사용자명"""
    admin_password: str
    """관리자 비밀번호"""


class S3Config(BaseModel):
    """S3 관련 설정 클래스"""
    
    endpoint: str
    """S3 엔드포인트"""
    access_key: str
    """Access Key"""
    secret_key: str
    """Secret Key"""
    secure: bool = False
    """HTTPS 사용 여부"""
    bucket_name: str
    """버킷 이름"""



class DocParserConfig(BaseModel):
    """Doc Parser 관련 설정 클래스"""
    
    api_url: str
    """Doc Parser API URL"""
    api_key: Optional[str] = None
    """API Key"""


class MemgraphConfig(BaseModel):
    """Memgraph 관련 설정 클래스 (GraphRAG)"""
    
    host: str
    """서버 IP"""
    port: int = 7687
    """Bolt 포트 번호"""
    username: Optional[str] = None
    """사용자명"""
    password: Optional[str] = None
    """비밀번호"""
    encrypted: bool = False
    """SSL/TLS 사용 여부"""


class ChatbotMessagesConfig(BaseModel):
    """챗봇 안내 문구 설정 클래스

    기능정의서 KAI-REQ-038/039/040이 요구하는 예외 상황 응답 문구입니다.
    운영 중 문구만 바꾸는 일이 잦아 코드가 아닌 설정으로 뺐습니다.
    """

    fallback: str = (
        "죄송합니다. 해당 질문에 대한 정보를 찾지 못했습니다. "
        "학사 관련 문의는 학사지원팀(053-580-5114)으로 문의해 주세요."
    )
    """답변하지 못하는 질문에 대한 양해 메시지 (KAI-REQ-040)"""
    abuse: str = (
        "저는 계명대학교 학사·취업 정보를 안내하는 챗봇입니다. "
        "학사 일정, 장학금, 졸업 요건, 취업 정보 등에 대해 질문해 주세요."
    )
    """비속어·장난·목적 외 발화에 대한 안내 문구 (KAI-REQ-038)"""
    ambiguous: str = (
        "질문을 조금 더 구체적으로 알려주시겠어요? "
        "예를 들어 \"2026학년도 1학기 수강신청 기간\"처럼 학기나 대상을 함께 적어주시면 정확히 안내할 수 있습니다."
    )
    """모호한 질문에 대한 되묻기 문구 (KAI-REQ-037)"""
    idle_closed: str = (
        "일정 시간 입력이 없어 대화를 종료합니다. 추가 문의가 있으시면 새로 질문해 주세요."
    )
    """미입력 자동 종료 안내 문구 (KAI-REQ-039)"""
    personal_data_unavailable: str = (
        "학번·성적·수강 이력 등 개인 정보 조회는 학내 시스템 연계가 완료된 후 제공될 예정입니다."
    )
    """학내 개인정보 연계 API 미제공 시 안내 문구 (KAI-REQ-003~012)"""
    attachment_required: str = (
        "첨부 파일 내용에 대한 질문이시군요. 이미지 또는 문서를 첨부한 뒤 다시 질문해 주세요."
    )
    """DOCUMENT 의도인데 첨부가 없을 때 안내 문구"""
    attachment_parse_failed: str = (
        "첨부하신 문서를 읽지 못했습니다. 파일이 손상되었거나 지원하지 않는 형식일 수 있습니다. "
        "다른 파일로 다시 시도해 주세요."
    )
    """문서 파싱 실패 안내 문구"""
    attachment_unavailable: str = (
        "첨부 파일을 불러오지 못했습니다. 파일을 다시 업로드한 뒤 질문해 주세요."
    )
    """S3 다운로드·소유권 검증 실패 안내 문구"""


class ChatbotConfig(BaseModel):
    """챗봇 관련 설정 클래스"""

    text_model: str
    """응답 생성에 사용할 텍스트 생성 모델명 (models 테이블에 등록된 이름)"""
    embedding_model: str
    """FAQ 지식베이스 임베딩에 사용할 모델명"""
    embedding_dim: int = 1024
    """임베딩 벡터 차원.

    `faq_embeddings.embedding` 컬럼과 HNSW 인덱스가 이 값으로 고정 생성되므로
    `embedding_model`이 실제로 내놓는 차원과 반드시 일치해야 합니다.
    값을 바꾸면 컬럼 타입과 인덱스를 재생성하고 전량 재색인해야 합니다.
    """
    top_k: int = 5
    """FAQ 검색 시 가져올 결과 수"""
    score_threshold: float = 0.35
    """FAQ 검색 최소 유사도 점수. 미달 시 미응답으로 기록합니다."""
    history_limit: int = 10
    """프롬프트에 포함할 최근 대화 메시지 수 (KAI-REQ-016 문맥 유지)"""
    evidence_max_chars: int = 12000
    """프롬프트 [검색 근거] 블록 전체에 넣을 최대 문자 수.

    규정 검색은 임계값 없이 상위 12건을 돌려주고 청크 하나가 최대 9천 자를 넘습니다.
    이를 그대로 이어 붙이면 모델 컨텍스트 상한을 넘겨 응답 생성이 400으로 실패합니다.
    """
    source_content_max_chars: int = 1500
    """근거 하나(규정 원문)에 넣을 최대 문자 수.

    긴 청크 하나가 예산을 다 써서 뒤 순위 근거가 통째로 빠지는 것을 막습니다.
    """
    history_max_chars: int = 6000
    """프롬프트에 넣을 대화 이력 전체 최대 문자 수 (최근 메시지 우선)"""
    query_max_chars: int = 8000
    """프롬프트에 넣을 사용자 질문 최대 문자 수.

    이력서 전문을 붙여넣는 등 질문 하나가 수만 자로 들어오는 경우를 잘라냅니다.
    """
    summary_trigger_count: int = 20
    """세션 메시지가 이 수를 넘으면 누적 요약을 갱신합니다. (KAI-REQ-041)"""
    ingestion_job_timeout_minutes: int = 60
    """수집 작업을 실패로 간주하는 기준 시간(분).

    수집 작업은 워커 프로세스 안에서 실행되므로 도중에 프로세스가 죽으면 작업이 `running`으로
    남아 이후 실행이 영구히 막힙니다. 이 시간을 넘긴 `running` 작업은 실패로 회수합니다.
    """
    abuse_keywords: list[str] = []
    """비속어·목적 외 발화 사전 필터 키워드 (KAI-REQ-038)

    이 필터는 LLM 분류보다 먼저 적용되어 곧바로 `abuse` 정형 문구로 끝납니다.
    "죽겠다", "미치겠다"처럼 힘들다는 뜻으로도 쓰이는 말을 넣으면 정서 지원이 필요한 학생이
    "부적절한 발화" 안내를 받게 되므로, 명백한 욕설만 등록하세요.
    """
    counseling_contact: Optional[str] = None
    """정서적 위기 신호가 보일 때 안내할 상담 창구 문구

    챗봇은 상담사가 아니므로, 자해·자살 암시 등 심각한 신호에는 공감만 하고 끝내지 않고
    실제 사람에게 연결될 통로를 함께 알려야 합니다. 학교마다 창구가 달라 코드에 두지 않고
    설정으로 받습니다. 비워 두면 상담 연결 안내 없이 공감만 수행합니다.
    """
    messages: ChatbotMessagesConfig = ChatbotMessagesConfig()
    """안내 문구 설정"""


class ExternalLinkConfig(BaseModel):
    """외부 서비스 연계 설정 클래스 (KAI-REQ-002/018/020/021)

    워크넷·청년정책포털 등은 별도 API Key 발급이 필요합니다. Key가 없으면
    `api_key`를 비워 두며, 이 경우 챗봇은 API 호출 대신 바로가기 링크만 안내합니다.
    """

    name: str
    """서비스 명칭 (예: 워크넷)"""
    url: str
    """바로가기 URL"""
    api_base: Optional[str] = None
    """API Base URL (미발급 시 None)"""
    api_key: Optional[str] = None
    """API Key (미발급 시 None)"""


class Config(BaseModel):
    """설정 관련 클래스"""

    database: DatabaseConfig
    """데이터베이스 관련 설정"""
    redis: RedisConfig
    """Redis 관련 설정"""
    auth: AuthConfig
    """사용자 인증 관련 설정"""
    s3: S3Config
    """S3 관련 설정"""
    doc_parser: DocParserConfig
    """Doc Parser 관련 설정"""
    memgraph: MemgraphConfig
    """Memgraph 관련 설정 (GraphRAG)"""
    chatbot: ChatbotConfig
    """챗봇 관련 설정"""
    external_links: list[ExternalLinkConfig] = []
    """외부 서비스 연계 설정"""
