from enum import Enum


class ModelProvider(str, Enum):
    """모델 제공자"""
    
    LOCAL = "local"
    """로컬 모델"""
    OPENAI_COMPATIBLE = "openai_compatible"
    """OpenAI Compatible"""
    OPENAI = "openai"
    """OpenAI"""
    ANTHROPIC = "anthropic"
    """Anthropic"""
    GEMINI = "gemini"
    """Google AI Studio - Gemini"""
    VERTEX_AI = "vertex_ai"
    """Google - Vertex AI"""
    XAI = "xai"
    """xAI"""
    MISTRAL = "mistral"
    """Mistral AI"""
    TOGETHER_AI = "together_ai"
    """Together AI"""
    GITHUB = "github"
    """GitHub"""
    GITHUB_COPILOT = "github_copilot"
    """GitHub Copilot"""
    AZURE = "azure"
    """Azure"""
    BEDROCK = "bedrock"
    """AWS - Bedrock"""
    SAGEMAKER = "sagemaker"
    """AWS - Sagemaker"""
    DASHSCOPE = "dashscope"
    """Dashscope"""
    FRIENDLY_AI = "friendliai"
    """Friendly AI"""
    
    
    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "local": "로컬 모델",
                "openai_compatible": "OpenAI Compatible",
                "openai": "OpenAI",
                "anthropic": "Anthropic",
                "gemini": "Google AI Studio - Gemini",
                "vertex_ai": "Google - Vertex AI",
                "xai": "xAI",
                "mistral": "Mistral AI",
                "together_ai": "Together AI",
                "github": "GitHub",
                "github_copilot": "GitHub Copilot",
                "azure": "Azure",
                "bedrock": "AWS - Bedrock",
                "sagemaker": "AWS - Sagemaker",
                "dashscope": "Dashscope",
                "friendliai": "Friendly AI",
            }
        })
        return json_schema


class ModelType(str, Enum):
    """모델 타입"""
    
    TEXT_GENERATION = "text_generation"
    """텍스트 생성 모델"""
    EMBEDDING = "embedding"
    """임베딩 모델"""
    RERANK = "rerank"
    """리랭크 모델"""
    
    
    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "text_generation": "텍스트 생성 모델",
                "embedding": "임베딩 모델",
                "rerank": "리랭크 모델",
            }
        })
        return json_schema


class ModelStatus(str, Enum):
    """모델 상태"""
    
    DOWNLOADING = "downloading"
    """다운로드 중"""
    LOADING = "loading"
    """로드 중"""
    RUNNING = "running"
    """실행 중"""
    STOPPING = "stopping"
    """중지 중"""
    STOPPED = "stopped"
    """중지됨"""
    ERROR = "error"
    """오류"""
    
    
    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "downloading": "다운로드 중",
                "loading": "로드 중",
                "running": "실행 중",
                "stopping": "중지 중",
                "stopped": "중지됨",
                "error": "오류",
            }
        })
        return json_schema


class ModelUsageStatus(str, Enum):
    """모델 사용량 상태"""
    
    SUCCESS = "success"
    """성공"""
    ERROR = "error"
    """오류"""
    
    
    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "success": "성공",
                "error": "오류",
            }
        })
        return json_schema


class GraphIndexStatus(str, Enum):
    """그래프 인덱싱 상태"""
    
    NO_INDEXED = "no_indexed"
    """인덱싱 안됨"""
    PROCESSING = "processing"
    """처리 중"""
    INDEXED = "indexed"
    """인덱싱 완료"""
    ERROR = "error"
    """오류"""
    
    
    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "no_indexed": "인덱싱 안됨",
                "processing": "처리 중",
                "indexed": "인덱싱 완료",
                "error": "오류",
            }
        })
        return json_schema


class DocumentStatus(str, Enum):
    """문서 처리 상태"""

    PROCESSING = "processing"
    """처리 중"""
    COMPLETED = "completed"
    """완료"""
    ERROR = "error"
    """오류"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "processing": "처리 중",
                "completed": "완료",
                "error": "오류",
            }
        })
        return json_schema


class RagEmbeddingStatus(str, Enum):
    """RAG 관리 화면용 임베딩/동기화 상태"""

    SYNCING = "syncing"
    """동기화 중"""
    SUCCESS = "success"
    """동기화 완료"""
    ERROR = "error"
    """에러"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "syncing": "동기화 중",
                "success": "동기화 완료",
                "error": "에러",
            }
        })
        return json_schema


class MaskingTargetField(str, Enum):
    """마스킹 대상 필드 분류"""

    STUDENT_ID = "student_id"
    """학번"""
    PHONE = "phone"
    """전화번호"""
    EMAIL = "email"
    """이메일"""
    RESIDENT_ID = "resident_id"
    """주민등록번호"""
    CUSTOM = "custom"
    """기타"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "student_id": "학번",
                "phone": "전화번호",
                "email": "이메일",
                "resident_id": "주민등록번호",
                "custom": "기타",
            }
        })
        return json_schema


class MaskingMethod(str, Enum):
    """마스킹 방식"""

    PARTIAL = "partial"
    """부분 마스킹 (앞·뒤 일부 보존)"""
    MIDDLE = "middle"
    """중간 마스킹"""
    FULL = "full"
    """전체 마스킹"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "partial": "부분 마스킹",
                "middle": "중간 마스킹",
                "full": "전체 마스킹",
            }
        })
        return json_schema


# ===== 계명대 챗봇 (KAI-REQ) =====

class Language(str, Enum):
    """지원 언어 (KAI-REQ-029 다국어 지원)"""

    KO = "ko"
    """한국어"""
    EN = "en"
    """영어"""
    ZH = "zh"
    """중국어"""
    VI = "vi"
    """베트남어"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "ko": "한국어",
                "en": "영어",
                "zh": "중국어",
                "vi": "베트남어",
            }
        })
        return json_schema


class FaqStatus(str, Enum):
    """FAQ 상태"""

    DRAFT = "draft"
    """임시 저장"""
    PUBLISHED = "published"
    """공개"""
    ARCHIVED = "archived"
    """보관"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "draft": "임시 저장",
                "published": "공개",
                "archived": "보관",
            }
        })
        return json_schema


class FaqVisibility(str, Enum):
    """FAQ 공개 범위"""

    PUBLIC = "public"
    """전체 공개 (비로그인 포함)"""
    STUDENT = "student"
    """재학생 공개"""
    INTERNAL = "internal"
    """교직원 전용"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "public": "전체 공개 (비로그인 포함)",
                "student": "재학생 공개",
                "internal": "교직원 전용",
            }
        })
        return json_schema


class VectorStatus(str, Enum):
    """벡터 색인 상태"""

    PENDING = "pending"
    """색인 대기"""
    INDEXED = "indexed"
    """색인 완료"""
    STALE = "stale"
    """원문 변경으로 재색인 필요"""
    FAILED = "failed"
    """색인 실패"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "pending": "색인 대기",
                "indexed": "색인 완료",
                "stale": "원문 변경으로 재색인 필요",
                "failed": "색인 실패",
            }
        })
        return json_schema


class SourceType(str, Enum):
    """지식 원천 유형"""

    FAQ = "faq"
    """FAQ"""
    NOTICE = "notice"
    """공지사항"""
    REGULATION = "regulation"
    """학칙·규정"""
    DOCUMENT = "document"
    """업로드 문서"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "faq": "FAQ",
                "notice": "공지사항",
                "regulation": "학칙·규정",
                "document": "업로드 문서",
            }
        })
        return json_schema


class ChatIntent(str, Enum):
    """감지 의도 (KAI-REQ-015/030/037/038)

    챗봇 그래프의 라우팅 기준이며, `retrieval_log.detected_intent`에 기록됩니다.
    """

    ACADEMIC = "academic"
    """학사 규정·제도·공지 문의 (RAG)"""
    CAREER = "career"
    """취업·진로·채용 문의 (RAG + 외부 연계)"""
    PERSONAL = "personal"
    """개인 학적·성적·비교과 등 개인 데이터 문의 (학내 API 연계)"""
    DOCUMENT = "document"
    """업로드 문서·이미지 기반 문의 (멀티모달)"""
    SMALL_TALK = "small_talk"
    """인사 등 일상 대화"""
    ABUSE = "abuse"
    """비속어·장난·서비스 목적과 무관한 발화"""
    UNKNOWN = "unknown"
    """의도 판별 불가 (모호한 질문)"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "academic": "학사 규정·제도·공지 문의 (RAG)",
                "career": "취업·진로·채용 문의 (RAG + 외부 연계)",
                "personal": "개인 학적·성적·비교과 등 개인 데이터 문의 (학내 API 연계)",
                "document": "업로드 문서·이미지 기반 문의 (멀티모달)",
                "small_talk": "인사 등 일상 대화",
                "abuse": "비속어·장난·서비스 목적과 무관한 발화",
                "unknown": "의도 판별 불가 (모호한 질문)",
            }
        })
        return json_schema


class ChatSessionStatus(str, Enum):
    """대화 세션 상태"""

    ACTIVE = "active"
    """진행 중"""
    IDLE_CLOSED = "idle_closed"
    """미입력으로 자동 종료 (KAI-REQ-039)"""
    CLOSED = "closed"
    """사용자 종료"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "active": "진행 중",
                "idle_closed": "미입력으로 자동 종료",
                "closed": "사용자 종료",
            }
        })
        return json_schema


class ChatRole(str, Enum):
    """대화 메시지 역할"""

    USER = "user"
    """사용자"""
    ASSISTANT = "assistant"
    """챗봇"""
    SYSTEM = "system"
    """시스템 안내"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "user": "사용자",
                "assistant": "챗봇",
                "system": "시스템 안내",
            }
        })
        return json_schema


class UnansweredReason(str, Enum):
    """미응답 사유 (KAI-REQ-040)"""

    NO_RESULT = "no_result"
    """검색 결과 없음"""
    LOW_SCORE = "low_score"
    """유사도 임계값 미달"""
    OUT_OF_SCOPE = "out_of_scope"
    """서비스 범위 밖 질문"""
    AMBIGUOUS = "ambiguous"
    """질문이 모호함"""
    MODEL_ERROR = "model_error"
    """모델 호출 오류"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "no_result": "검색 결과 없음",
                "low_score": "유사도 임계값 미달",
                "out_of_scope": "서비스 범위 밖 질문",
                "ambiguous": "질문이 모호함",
                "model_error": "모델 호출 오류",
            }
        })
        return json_schema


class ReviewStatus(str, Enum):
    """미응답 질문 검토 상태"""

    PENDING = "pending"
    """검토 대기"""
    IN_REVIEW = "in_review"
    """검토 중"""
    RESOLVED = "resolved"
    """조치 완료 (FAQ 등록 등)"""
    REJECTED = "rejected"
    """조치 불필요"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "pending": "검토 대기",
                "in_review": "검토 중",
                "resolved": "조치 완료 (FAQ 등록 등)",
                "rejected": "조치 불필요",
            }
        })
        return json_schema


class IngestionStatus(str, Enum):
    """수집 작업 상태"""

    PENDING = "pending"
    """대기"""
    RUNNING = "running"
    """실행 중"""
    COMPLETED = "completed"
    """완료"""
    FAILED = "failed"
    """실패"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "pending": "대기",
                "running": "실행 중",
                "completed": "완료",
                "failed": "실패",
            }
        })
        return json_schema


class IngestionItemStatus(str, Enum):
    """수집 작업 항목 처리 상태"""

    PENDING = "pending"
    """대기"""
    SUCCESS = "success"
    """성공"""
    FAILED = "failed"
    """실패"""
    SKIPPED = "skipped"
    """변경 없음으로 건너뜀"""


    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        json_schema = handler(core_schema)
        json_schema.update({
            "x-enumDescriptions": {
                "pending": "대기",
                "success": "성공",
                "failed": "실패",
                "skipped": "변경 없음으로 건너뜀",
            }
        })
        return json_schema
