import litellm

from dataclasses import dataclass

from logging import Logger

from typing import Any, Optional, TypedDict

from langchain_core.runnables import RunnableConfig

from langgraph.graph import StateGraph, START, END

from app.config import config as app_config
from app.models.auth import TokenUserInfo
from app.models.enum import (
    ChatIntent,
    Language,
    ModelStatus,
    ModelType,
    ModelUsageStatus,
    SourceType,
    UnansweredReason,
)
from app.models.cost import TextGenerationUsage
from app.utils.database import DatabaseManager
from app.utils.faq_service import FAQ_COLLECTION_NAME, FaqKnowledgeBaseNotReady, FaqSearchResult, search_faq
from app.utils.regulation_ingest import REGULATION_COLLECTION_NAME, RegulationSearchResult, search_regulations
from app.utils.litellm import (
    build_chat_template_kwargs,
    extract_token_counts,
    get_litellm_model_name,
    get_litellm_params,
    save_usage,
)
from app.utils.logger import get_logger
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.common as util
import app.utils.faq_service as faq_service
from app.utils.chat_attachments import build_user_content


logger = get_logger("chat_graph", log_dir="logs")


async def _collection_retrieval_settings(
    db_manager: DatabaseManager,
    collection_name: str,
) -> tuple[int, float]:
    """컬렉션에 저장된 top_k / similarity_threshold를 반환합니다. 없으면 chatbot 기본값."""

    query = (db_models.Collection
             .select()
             .where(db_models.Collection.name == collection_name))
    collection: Optional[db_items.Collection] = await db_manager.select_item(query)
    top_k = app_config.chatbot.top_k
    threshold = app_config.chatbot.score_threshold
    if collection is not None:
        if getattr(collection, "top_k", None):
            top_k = int(collection.top_k)
        if getattr(collection, "similarity_threshold", None) is not None:
            threshold = float(collection.similarity_threshold)
    return top_k, threshold


RETRIEVAL_INTENTS = (ChatIntent.ACADEMIC, ChatIntent.CAREER)
"""지식베이스 검색(FAQ + 학칙·규정)을 수행할 의도 목록 (KAI-REQ-013/015)

DOCUMENT는 업로드 첨부를 근거로 답하므로 검색 대상에서 제외합니다.
"""

LANGUAGE_LABELS = {
    Language.KO: "한국어",
    Language.EN: "English",
    Language.ZH: "中文",
    Language.VI: "Tiếng Việt",
}
"""응답 언어 지시에 사용할 언어 표기 (KAI-REQ-029 다국어 지원)"""

CLASSIFY_MAX_TOKENS = 384
"""의도·언어 판정 응답 토큰 상한

받아야 할 값은 `academic|ko` 한 줄뿐이지만 상한을 크게 잡습니다. gemma-4-31b-it은
thinking을 끌 수 없고(상위 CLAUDE.md 참고) 사고 과정도 출력 토큰을 소비하는데,
예전 값(8)에서는 사고 과정만으로 상한에 걸려 **content가 항상 빈 문자열**로 돌아왔습니다.
(실측: output_tokens=5 / reasoning_tokens=5 → 한국어 질문까지 전부 unknown으로 분류되어
되묻기로 빠지고 있었음)

실측한 사고 토큰은 0~253개로 편차가 큽니다. 혼용 문장처럼 판단이 갈리는 발화일수록
길게 생각합니다. 상한에 걸리면 응답이 비고, 그때는 표기 문자 기반 추정으로 물러섭니다.
(`guess_language_by_script`) 상한을 더 키우면 그만큼 첫 응답까지의 지연이 늘어나므로,
"대부분을 담되 폭주는 끊는" 값으로 둡니다.
"""

SUMMARY_MAX_TOKENS = 512
"""세션 요약 응답 토큰 상한"""

EMOTION_GENERATION_MAX_TOKENS = 1024
"""정서 지원(EMOTION) 최종 응답 토큰 상한.

일반 학사·취업 안내는 간결하게 두고, emotion만 공감·인정에 더 많은 분량을 쓰도록
프롬프트와 함께 상한을 넉넉히 둡니다. (기본 응답은 max_tokens를 지정하지 않음)
"""

TRUNCATION_MARKER = " …(이하 생략)"
"""길이 예산으로 잘라낸 텍스트 끝에 붙이는 표시.

모델이 문장이 끊긴 것을 알아야 잘린 뒷부분을 임의로 채워 넣지 않습니다.
"""

CLASSIFY_QUERY_MAX_CHARS = 1000
"""의도 분류에 넣을 질문 최대 문자 수. 라벨 판단에는 앞부분만 있으면 충분합니다."""

CONDENSE_QUERY_MAX_CHARS = 1000
"""후속 질문 재작성에 넣을 질문 최대 문자 수"""

CONDENSE_HISTORY_ITEM_MAX_CHARS = 500
"""후속 질문 재작성에 넣을 이력 메시지 하나의 최대 문자 수"""

SUMMARY_INPUT_MAX_CHARS = 8000
"""세션 요약 입력 전체 최대 문자 수"""


CLASSIFY_SYSTEM_PROMPT = (
    "당신은 계명대학교 학생 지원 챗봇의 분류기입니다.\n"
    "사용자 발화 하나를 읽고 **의도**와 **답변 언어**를 함께 판정합니다.\n"
    "\n"
    "[의도 라벨]\n"
    "- academic: 학사 규정, 수강신청, 장학금, 졸업요건, 학사일정, 교내 제도·공지 문의\n"
    "- career: 취업, 진로, 채용, 인턴, 자격증, 취업 지원 프로그램 문의.\n"
    "  이력서·자기소개서·포트폴리오 첨삭이나 면접 준비 요청도 career입니다.\n"
    "- personal: 내 학번·성적·수강 내역·등록금 납부 등 본인 개인 데이터 조회 요청\n"
    "- document: 사용자가 업로드한 첨부파일(문서·이미지) 자체의 내용에 대한 문의.\n"
    "  이력서 본문처럼 글을 대화에 직접 붙여넣고 검토를 요청한 경우는 document가 아니라 career입니다.\n"
    "- emotion: 힘들다·슬프다·불안하다·지친다·포기하고 싶다처럼 **본인의 감정이나 어려움을 털어놓는 발화**.\n"
    "  학업·성적·진로 부담을 호소하는 경우도 emotion입니다. 정보를 묻는 것이 아니라 마음을 이야기하는 발화입니다.\n"
    "- small_talk: 인사, 감사, 잡담 등 일상 대화\n"
    "- abuse: 비속어, 욕설, 장난, 서비스 목적과 무관한 발화\n"
    "- unknown: 위 어디에도 해당하지 않거나 의미를 알 수 없는 모호한 발화\n"
    "\n"
    "[라벨이 겹칠 때]\n"
    "- 감정 호소와 정보 질문이 함께 있으면, 물음표로 끝나는 구체적 질문이 있을 때만 academic·career이고\n"
    "  그렇지 않으면 emotion입니다. (예: \"학점 부담돼서 너무 힘들어\" -> emotion)\n"
    "- 감정이 드러난 발화는 small_talk이나 unknown으로 두지 말고 emotion으로 분류합니다.\n"
    "- 감정 표현에 비속어가 섞여 있어도, 챗봇을 겨냥한 욕설이 아니면 abuse가 아니라 emotion입니다.\n"
    "\n"
    "[답변 언어 라벨]\n"
    "ko(한국어) / en(영어) / zh(중국어) / vi(베트남어) 중 발화의 주된 언어를 고릅니다.\n"
    "- 한국어와 외국어가 섞이면 조사·어미가 한국어일 때 ko, 한국어가 고유명사뿐일 때 그 외국어입니다.\n"
    "- 위 네 언어가 아니면 en입니다.\n"
    "\n"
    "출력은 `의도|언어` 형식의 한 줄뿐입니다. 예: academic|ko\n"
    "고민하지 말고 곧바로 한 줄만 출력하세요. 설명이나 따옴표를 덧붙이지 마세요."
)
"""의도·응답 언어 통합 분류 시스템 프롬프트 (KAI-REQ-029/030)

언어 판정을 별도 LLM 호출로 두지 않고 의도 분류에 합친 이유는, 챗봇 한 턴에 이미
재작성·분류·응답·요약으로 여러 번 모델을 호출하고 있어 호출을 더 늘리면 첫 응답까지의
지연과 비용이 그만큼 커지기 때문입니다. 두 판정 모두 "발화 한 줄을 보고 라벨을 고르는"
같은 성격의 작업이라 한 번의 호출로 충분합니다.
"""

CONDENSE_MAX_TOKENS = 96
"""후속 질문 재작성 응답 토큰 상한. 한 문장짜리 질문만 받으면 되므로 짧게 제한합니다.

주의: 이 상한 때문에 **현재 재작성은 사실상 동작하지 않습니다.** gemma-4-31b-it은 thinking을
끌 수 없어 사고 과정이 먼저 출력 토큰을 쓰는데, 이 작업은 사고가 길어 상한에 걸립니다.
(실측: 96 상한에서 93/93, 320으로 올려도 317/317 — 둘 다 사고만 하다 잘려 결과가 빈 문자열)
결과가 비면 원문으로 검색을 이어가므로 동작은 유지되지만, 후속 질문의 맥락 복원은 되지 않습니다.
상한을 더 올리면 재작성 한 번에 10초 이상이 들어 첫 응답이 그만큼 늦어지므로, 값을 키우는 것으로는
해결되지 않습니다. (사고를 덜 하게 만드는 프롬프트 재설계나 다른 모델이 필요합니다)
`CLASSIFY_MAX_TOKENS`와 달리 이 값을 올리지 않은 이유가 이것입니다.
"""

CONDENSE_HISTORY_LIMIT = 6
"""후속 질문 재작성에 참고할 직전 대화 턴 수 (최근 3왕복)"""

CONDENSE_SYSTEM_PROMPT = (
    "당신은 계명대학교 학생 지원 챗봇의 질문 재작성기입니다.\n"
    "이전 대화를 참고해, 마지막 사용자 발화를 **그 자체로 의미가 통하는 하나의 질문**으로 다시 씁니다.\n"
    "\n"
    "규칙:\n"
    "- '그거', '그럼', '거기', '위에서 말한' 같은 지시어와 생략된 주어를 이전 대화의 실제 대상으로 바꿉니다.\n"
    "- 이전 대화에 없는 정보를 새로 지어내지 않습니다.\n"
    "- 마지막 발화가 이미 그 자체로 완결된 질문이면 원문을 그대로 출력합니다.\n"
    "- 인사·잡담처럼 검색이 필요 없는 발화도 원문을 그대로 출력합니다.\n"
    "- **마지막 발화의 언어를 그대로 유지합니다. 다른 언어로 번역하지 마세요.**\n"
    "- 재작성한 질문 한 문장만 출력하고, 설명이나 따옴표를 덧붙이지 않습니다."
)
"""후속 질문 재작성 시스템 프롬프트

대화 맥락 없이 "그럼 기간은?"을 그대로 임베딩하면 검색이 실패합니다.
지식베이스 검색 전에 질문을 독립적인 형태로 복원해야 근거를 찾을 수 있습니다.
"""

def _shot(query: str, intent: ChatIntent, language: Language) -> tuple[str, str]:
    """Few-shot 예시 한 쌍을 `발화 -> "의도|언어"` 형태로 만듭니다."""

    return query, f"{intent.value}|{language.value}"


CLASSIFY_FEW_SHOTS: list[tuple[str, str]] = [
    _shot("2026학년도 1학기 수강신청 기간이 언제야?", ChatIntent.ACADEMIC, Language.KO),
    _shot("졸업하려면 학점 몇 점 들어야 해?", ChatIntent.ACADEMIC, Language.KO),
    _shot("교내 채용 설명회 일정 알려줘", ChatIntent.CAREER, Language.KO),
    _shot("자기소개서 첨삭 프로그램 있어?", ChatIntent.CAREER, Language.KO),
    _shot("아래 이력서 한번 봐줄래? ## 1. 지원 동기 저는 백엔드 개발자를 지망하여", ChatIntent.CAREER, Language.KO),
    _shot("내 이번 학기 성적 알려줘", ChatIntent.PERSONAL, Language.KO),
    _shot("내가 지금까지 들은 전공 학점 몇이야?", ChatIntent.PERSONAL, Language.KO),
    _shot("방금 올린 PDF에서 제출 기한만 정리해줘", ChatIntent.DOCUMENT, Language.KO),
    # 감정 호소. 학사 어휘("학점", "시험")가 섞여도 질문이 아니면 academic이 아니라는 것을 보여 줍니다.
    _shot("나 사실 조금 너무 슬퍼.. 의예과라.. 학점을 너무 많이 들어야해...", ChatIntent.EMOTION, Language.KO),
    _shot("시험 망친 것 같아서 너무 불안해", ChatIntent.EMOTION, Language.KO),
    _shot("다 그만두고 휴학하고 싶다 진짜 지친다", ChatIntent.EMOTION, Language.KO),
    _shot("안녕! 넌 누구야?", ChatIntent.SMALL_TALK, Language.KO),
    _shot("야 이 멍청한 봇아", ChatIntent.ABUSE, Language.KO),
    _shot("그거 어떻게 해?", ChatIntent.UNKNOWN, Language.KO),
    # 한국어 이외 언어 예시. 유학생이 실제로 쓰는 표현이라 의도 라벨은 한국어와 같아야 하고
    # 언어 라벨만 달라져야 한다는 점을 모델에 보여 줍니다. (KAI-REQ-029)
    _shot("How do I apply for a leave of absence?", ChatIntent.ACADEMIC, Language.EN),
    _shot("Where can I get help with my resume?", ChatIntent.CAREER, Language.EN),
    _shot("I feel so overwhelmed with all these classes", ChatIntent.EMOTION, Language.EN),
    _shot("请问奖学金怎么申请？", ChatIntent.ACADEMIC, Language.ZH),
    _shot("Thời gian đăng ký môn học là khi nào?", ChatIntent.ACADEMIC, Language.VI),
    _shot("Xin chào, bạn là ai?", ChatIntent.SMALL_TALK, Language.VI),
    # 혼용 문장. 어느 쪽 언어가 문장의 뼈대인지로 갈린다는 것을 두 예시로 대조해 보여 줍니다.
    _shot("수강신청 deadline이 언제야?", ChatIntent.ACADEMIC, Language.KO),
    _shot("When is the 수강신청 deadline?", ChatIntent.ACADEMIC, Language.EN),
]
"""의도·언어 통합 분류 Few-shot 예시 (KAI-REQ-029/030)"""


class ChatGraphState(TypedDict, total=False):
    """챗봇 오케스트레이션 그래프 상태

    그래프는 라우팅·검색·프롬프트 구성까지만 책임지고, 최종 텍스트 스트리밍은
    엔드포인트가 `messages`를 받아 `stream_chat_completion`으로 수행합니다.
    (async generator를 그래프 밖으로 흘리기 어렵기 때문입니다.)
    """

    query: str
    """사용자 질문 (원문)"""
    search_query: str
    """검색·분류에 사용할 질문

    후속 질문("그럼 기간은?")은 원문만으로 임베딩하면 검색이 실패하므로,
    직전 대화를 참고해 독립적인 질문으로 재작성한 결과를 담습니다.
    첫 턴이거나 재작성이 불필요하면 `query`와 같습니다.
    """
    query_condensed: bool
    """후속 질문 재작성이 실제로 일어났는지 여부 (검색 로그·디버깅용)"""
    session_id: str
    """세션 ID"""
    message_id: str
    """사용자 메시지 ID"""
    language: Language
    """응답 언어

    `language_explicit`가 False면 `classify_intent`가 사용자 발화를 보고 덮어씁니다.
    """
    language_explicit: bool
    """사용자가 응답 언어를 직접 고른 상태인지 여부

    True면 자동 감지 결과보다 지정 언어가 우선합니다. UI에서 언어를 골랐다는 것은
    "질문은 영어로 하지만 답은 한국어로 받겠다" 같은 의도일 수 있어 뒤집으면 안 됩니다.
    """
    detected_language: Optional[Language]
    """자동 감지된 발화 언어 (명시 지정이어도 기록은 남깁니다)"""
    history: list[dict]
    """최근 대화 이력 (`{"role": ..., "content": ...}`)"""
    attachments: list
    """이번 턴에 해석된 첨부 목록 (`ResolvedAttachment`). 이력에는 넣지 않습니다."""
    summary: Optional[str]
    """세션 누적 요약"""
    message_count: int
    """세션 메시지 수 (요약 트리거 판단용)"""
    intent: ChatIntent
    """감지 의도"""
    intent_error: bool
    """의도 분류 모델 호출 실패 여부"""
    faq_results: list[FaqSearchResult]
    """FAQ 검색 결과"""
    regulation_results: list[RegulationSearchResult]
    """학칙·규정 hybrid 검색 결과"""
    sources: list[dict]
    """응답 근거 목록 (`chat_messages.sources`에 저장)"""
    messages: list[dict]
    """최종 응답 생성을 위한 LLM 메시지 목록 (엔드포인트가 스트리밍에 사용)"""
    answer: Optional[str]
    """정해진 문구 응답 (abuse/fallback/ambiguous/personal). 생성이 필요하면 None"""
    needs_generation: bool
    """LLM 스트리밍 생성이 필요한지 여부"""
    unanswered_reason: Optional[UnansweredReason]
    """미응답 사유 (KAI-REQ-040)"""
    retrieval_attempted: bool
    """FAQ 검색을 시도했는지 여부 (`retrieval_logs` 기록 여부 판단)"""
    retrieval_latency_ms: int
    """FAQ 검색 지연 시간(ms)"""
    retrieval_error: Optional[str]
    """FAQ 검색 실패 사유 (벡터 검색·임베딩 모델 미준비 등)"""
    service_unavailable: bool
    """의존 서비스(벡터 검색·모델) 미준비 여부"""
    summary_updated: Optional[str]
    """갱신된 세션 요약 (없으면 None)"""


@dataclass
class ChatGraphDeps:
    """그래프 노드가 사용하는 런타임 의존성

    DB 매니저처럼 직렬화할 수 없는 객체는 상태가 아니라 `configurable`로 주입합니다.
    """

    db_manager: DatabaseManager
    """데이터베이스 매니저 (FAQ 벡터 검색도 여기서 수행합니다.)"""
    user_info: TokenUserInfo
    """사용자 정보"""
    logger: Optional[Logger] = None
    """API 로거 (없으면 모듈 로거 사용)"""


def _deps(config: Optional[RunnableConfig]) -> ChatGraphDeps:
    """그래프 실행 설정에서 런타임 의존성을 꺼냅니다.

    Args:
        config (Optional[RunnableConfig]): LangGraph 실행 설정

    Returns:
        ChatGraphDeps: 런타임 의존성

    Raises:
        ValueError: 의존성이 주입되지 않은 경우
    """

    deps = (config or {}).get("configurable", {}).get("deps")
    if not isinstance(deps, ChatGraphDeps):
        raise ValueError("챗봇 그래프 실행에 필요한 의존성이 주입되지 않았습니다.")
    return deps


def _log(deps: ChatGraphDeps) -> Logger:
    """사용할 로거를 반환합니다."""

    return deps.logger or logger


def _language_label(language: Optional[Language]) -> str:
    """언어 코드에 대응하는 표기를 반환합니다.

    Args:
        language (Optional[Language]): 언어

    Returns:
        str: 언어 표기 (미지정 시 한국어)
    """

    if isinstance(language, str) and not isinstance(language, Language):
        try:
            language = Language(language)
        except ValueError:
            language = Language.KO
    return LANGUAGE_LABELS.get(language or Language.KO, LANGUAGE_LABELS[Language.KO])


USE_LINKS_TEXT = {
    Language.KO: "아래 바로가기를 이용해 주세요.",
    Language.EN: "Please use the links below.",
    Language.ZH: "请使用以下快捷链接。",
    Language.VI: "Vui lòng sử dụng các liên kết dưới đây.",
}
"""개인정보 안내에 덧붙이는 바로가기 안내 문장 (KAI-REQ-029)"""


def _use_links_text(language: Optional[Language]) -> str:
    """바로가기 안내 문장을 응답 언어로 반환합니다."""

    return USE_LINKS_TEXT.get(language or Language.KO, USE_LINKS_TEXT[Language.KO])


def _external_links_text() -> str:
    """외부 서비스 바로가기 목록을 문자열로 구성합니다. (KAI-REQ-002/021)

    Returns:
        str: 바로가기 목록 (설정이 없으면 빈 문자열)
    """

    links = [f"- {link.name}: {link.url}" for link in (app_config.external_links or [])]
    return "\n".join(links)


def _truncate(text: Optional[str], limit: int) -> str:
    """문자 수 예산에 맞춰 텍스트를 자릅니다.

    모델 컨텍스트 상한을 넘긴 요청은 400으로 통째로 실패하므로, 프롬프트에 들어가는
    가변 길이 텍스트(질문·이력·근거 원문)는 모두 이 함수를 거칩니다.

    Args:
        text (Optional[str]): 원본 텍스트
        limit (int): 최대 문자 수 (0 이하면 자르지 않음)

    Returns:
        str: 예산에 맞게 자른 텍스트
    """

    text = (text or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + TRUNCATION_MARKER


def match_abuse_keywords(text: str) -> Optional[str]:
    """비속어 사전 필터에 걸리는 키워드를 찾습니다. (KAI-REQ-038)

    LLM 분류보다 먼저 적용해 명백한 비속어에 모델 호출 비용을 쓰지 않도록 합니다.

    Args:
        text (str): 사용자 발화

    Returns:
        Optional[str]: 매칭된 키워드 또는 None
    """

    lowered = (text or "").lower()
    for keyword in app_config.chatbot.abuse_keywords or []:
        keyword = (keyword or "").strip().lower()
        if keyword and keyword in lowered:
            return keyword
    return None


def parse_intent(text: Optional[str]) -> ChatIntent:
    """모델 응답에서 의도 라벨을 파싱합니다.

    라벨 외 문장이 섞여 오더라도 포함된 라벨을 찾아내고, 실패하면 `UNKNOWN`을 반환합니다.

    Args:
        text (Optional[str]): 모델 응답

    Returns:
        ChatIntent: 감지 의도
    """

    normalized = (text or "").strip().lower()
    if not normalized:
        return ChatIntent.UNKNOWN

    for intent in ChatIntent:
        if normalized == intent.value:
            return intent

    # small_talk 처럼 언더스코어가 포함된 라벨이 먼저 매칭되도록 긴 라벨부터 확인
    for intent in sorted(ChatIntent, key=lambda item: len(item.value), reverse=True):
        if intent.value in normalized:
            return intent
    return ChatIntent.UNKNOWN


def parse_intent_and_language(text: Optional[str]) -> tuple[ChatIntent, Optional[Language]]:
    """모델 응답에서 `의도|언어`를 파싱합니다.

    언어 라벨이 없거나 지원하지 않는 값이면 언어는 None으로 돌려주고, 호출자가 표기 문자
    기반 추정으로 물러섭니다. 언어를 못 읽었다고 의도까지 버릴 이유는 없기 때문입니다.

    Args:
        text (Optional[str]): 모델 응답 (예: "academic|ko")

    Returns:
        tuple[ChatIntent, Optional[Language]]: (감지 의도, 감지 언어 또는 None)
    """

    normalized = (text or "").strip().lower()
    if not normalized:
        return ChatIntent.UNKNOWN, None

    # 모델이 여러 줄을 뱉는 경우 라벨이 있는 첫 줄만 씁니다.
    line = next((item.strip() for item in normalized.splitlines() if item.strip()), "")
    intent_part, _, language_part = line.partition("|")

    language: Optional[Language] = None
    candidate = language_part.strip().strip(".`\"' ")
    if candidate:
        try:
            language = Language(candidate)
        except ValueError:
            language = None

    return parse_intent(intent_part or line), language


VIETNAMESE_MARKS = set("ăâđêôơưĂÂĐÊÔƠƯàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩị"
                       "òóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ")
"""베트남어 고유의 성조·모음 표기 문자

라틴 문자만으로는 영어와 베트남어를 가를 수 없어, 베트남어에만 나타나는 글자를 신호로 씁니다.
"""


def guess_language_by_script(text: Optional[str]) -> Language:
    """표기 문자만 보고 응답 언어를 추정합니다.

    LLM 판정이 실패했을 때(모델 장애·빈 응답·라벨 누락) 쓰는 안전망입니다. 이 경로가 있어야
    모델이 흔들려도 최소한 "질문한 문자로 답한다"는 동작이 유지됩니다.

    혼용 문장은 한글이 라틴 문자보다 지나치게 적을 때만 외국어로 봅니다.
    "수강신청 deadline이 언제야?"처럼 조사·어미가 한글로 남아 있으면 한글 비중이 높고,
    "When is the 수강신청 deadline?"처럼 고유명사만 한글이면 비중이 낮기 때문입니다.
    한글 한 글자를 라틴 문자 3자와 같게 세는데, 한글은 음절 단위이고 라틴 문자는 낱자 단위라
    그대로 비교하면 같은 분량의 한국어가 항상 불리하게 나오기 때문입니다.

    Args:
        text (Optional[str]): 사용자 발화

    Returns:
        Language: 추정 언어 (근거가 없으면 서비스 기본값인 한국어)
    """

    text = text or ""
    # 한글 음절(U+AC00~U+D7A3)과 호환 자모(U+3130~U+318F, "ㅋㅋ" 같은 표현)를 함께 셉니다.
    hangul = sum(1 for char in text if "가" <= char <= "힣" or "㄰" <= char <= "㆏")
    han = sum(1 for char in text if "一" <= char <= "鿿")
    latin = sum(1 for char in text if char.isascii() and char.isalpha())
    vietnamese = sum(1 for char in text if char in VIETNAMESE_MARKS)

    if hangul and hangul * 3 >= latin:
        return Language.KO
    if vietnamese:
        return Language.VI
    if han:
        # 한국어 발화에도 한자가 섞일 수 있으나, 그 경우는 위에서 이미 한국어로 걸러집니다.
        return Language.ZH
    if latin:
        return Language.EN
    return Language.KO


CANNED_MESSAGE_TRANSLATIONS: dict[str, dict[Language, str]] = {
    "fallback": {
        Language.EN: ("Sorry, I couldn't find information for that question. "
                      "For academic inquiries, please contact the Academic Affairs Team (053-580-5114)."),
        Language.ZH: "抱歉，未能找到该问题的相关信息。学业相关咨询请联系学务支援组（053-580-5114）。",
        Language.VI: ("Xin lỗi, tôi không tìm thấy thông tin cho câu hỏi này. "
                      "Về các vấn đề học vụ, vui lòng liên hệ Phòng Hỗ trợ Học vụ (053-580-5114)."),
    },
    "abuse": {
        Language.EN: ("I'm a chatbot that provides Keimyung University academic and career information. "
                      "Please ask about academic schedules, scholarships, graduation requirements, or careers."),
        Language.ZH: "我是提供启明大学学务与就业信息的聊天机器人。请咨询学事日程、奖学金、毕业条件、就业信息等。",
        Language.VI: ("Tôi là chatbot cung cấp thông tin học vụ và việc làm của Đại học Keimyung. "
                      "Vui lòng hỏi về lịch học vụ, học bổng, điều kiện tốt nghiệp hoặc việc làm."),
    },
    "ambiguous": {
        Language.EN: ("Could you be a bit more specific? For example, adding the semester or subject "
                      "(like \"course registration period for spring 2026\") helps me answer accurately."),
        Language.ZH: "能否请您说得更具体一些？例如写明学期或对象（如“2026学年第1学期选课时间”），我才能准确回答。",
        Language.VI: ("Bạn có thể nói cụ thể hơn không? Ví dụ, nếu ghi rõ học kỳ hoặc đối tượng "
                      "(như \"thời gian đăng ký môn học học kỳ 1 năm 2026\") thì tôi sẽ trả lời chính xác hơn."),
    },
    "idle_closed": {
        Language.EN: "The conversation was closed after a period of inactivity. Please ask again to start a new one.",
        Language.ZH: "由于长时间无输入，对话已结束。如有其他问题，请重新提问。",
        Language.VI: "Cuộc trò chuyện đã kết thúc do không có hoạt động. Hãy đặt câu hỏi mới để bắt đầu lại.",
    },
    "personal_data_unavailable": {
        Language.EN: ("Lookups of personal data such as student ID, grades, and course history will be available "
                      "once the integration with the university system is complete."),
        Language.ZH: "学号、成绩、修课记录等个人信息查询将在校内系统对接完成后提供。",
        Language.VI: ("Việc tra cứu thông tin cá nhân như mã số sinh viên, điểm số, lịch sử học phần sẽ được "
                      "cung cấp sau khi hoàn tất kết nối với hệ thống của trường."),
    },
    "attachment_required": {
        Language.EN: ("That looks like a question about an uploaded file. "
                      "Please attach an image or document and ask again."),
        Language.ZH: "这似乎是关于已上传文件的问题。请先附上图片或文档后再提问。",
        Language.VI: ("Có vẻ bạn đang hỏi về tệp đã tải lên. "
                      "Vui lòng đính kèm hình ảnh hoặc tài liệu rồi hỏi lại."),
    },
    "attachment_parse_failed": {
        Language.EN: ("I couldn't read the attached document. The file may be damaged or unsupported. "
                      "Please try again with a different file."),
        Language.ZH: "无法读取您附上的文档。文件可能已损坏或不被支持。请更换文件后重试。",
        Language.VI: ("Tôi không đọc được tài liệu đính kèm. Tệp có thể bị hỏng hoặc không được hỗ trợ. "
                      "Vui lòng thử lại với tệp khác."),
    },
    "attachment_format_unsupported": {
        Language.EN: ("This file type (e.g. DOCX/HWP) isn't supported yet. "
                      "Please save it as PDF or an image and attach it again."),
        Language.ZH: "暂不支持该文件格式（如 DOCX/HWP）。请另存为 PDF 或图片后重新上传。",
        Language.VI: ("Chưa hỗ trợ định dạng này (DOCX/HWP...). "
                      "Vui lòng lưu thành PDF hoặc ảnh rồi đính kèm lại."),
    },
    "attachment_unavailable": {
        Language.EN: "I couldn't load the attachment. Please upload the file again and ask your question.",
        Language.ZH: "无法加载附件。请重新上传文件后再提问。",
        Language.VI: "Tôi không tải được tệp đính kèm. Vui lòng tải lại tệp rồi đặt câu hỏi.",
    },
}
"""정형 안내 문구의 언어별 대응 문구 (KAI-REQ-029)

`app_config.chatbot.messages`는 한국어 한 벌만 담는 구조라, 외국어로 질문한 사용자에게도
한국어 안내가 그대로 나갑니다. 이 문구들은 LLM이 생성하지 않고 그대로 전달되는 값이라
번역할 기회가 아예 없으므로, 코드에 언어별 문구를 함께 두고 골라 씁니다.

한국어는 여기 두지 않습니다. 운영 중 문구 수정은 설정으로 하는 것이 원칙이라
(`ChatbotMessagesConfig` 주석 참고) 기본 언어만큼은 설정값을 그대로 써야 하기 때문입니다.
설정에서 한국어 문구를 고친 경우 외국어 문구는 따라 바뀌지 않는다는 점이 이 구조의 한계입니다.
"""


def localized_message(key: str, language: Optional[Language]) -> str:
    """정형 안내 문구를 응답 언어에 맞춰 반환합니다.

    Args:
        key (str): `app_config.chatbot.messages`의 필드명
        language (Optional[Language]): 응답 언어

    Returns:
        str: 안내 문구 (해당 언어 문구가 없으면 설정에 있는 한국어 문구)
    """

    default = getattr(app_config.chatbot.messages, key, "") or ""
    if language is None or language == Language.KO:
        return default
    return CANNED_MESSAGE_TRANSLATIONS.get(key, {}).get(language, default)


async def complete_text(
    model_name: str,
    messages: list[dict],
    user_info: TokenUserInfo,
    db_manager: DatabaseManager,
    max_tokens: Optional[int] = None,
    temperature: float = 0.0,
    usage_source: str = "chatbot",
) -> str:
    """보조 LLM 호출(의도 분류·요약)을 수행하고 텍스트만 반환합니다.

    최종 응답 생성은 `stream_chat_completion`이 담당하며, 이 함수는 스트리밍이 필요 없는
    짧은 내부 호출 전용입니다. 사용량은 동일하게 `save_usage`로 기록해 대시보드 집계를 유지합니다.

    Args:
        model_name (str): 모델명 (models 테이블 등록명)
        messages (list[dict]): 요청 메시지 목록
        user_info (TokenUserInfo): 사용자 정보
        db_manager (DatabaseManager): 데이터베이스 매니저
        max_tokens (Optional[int]): 응답 토큰 상한 (Default: None)
        temperature (float): 샘플링 온도 (Default: 0.0)
        usage_source (str): 사용량 기록에 남길 호출 출처 (Default: "chatbot")

    Returns:
        str: 모델 응답 텍스트

    Raises:
        ValueError: 등록되지 않았거나 실행 중이 아닌 모델인 경우
    """

    query = (db_models.Model.select().where(
        (db_models.Model.name == model_name) &
        (db_models.Model.status == ModelStatus.RUNNING.value)
    ))
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not model:
        raise ValueError(f"등록되지 않은 모델이거나 실행중이 아닙니다: model={model_name}")

    completion_params: dict[str, Any] = {
        "model": get_litellm_model_name(model.provider, model.name, model.model_id),
        "messages": messages,
        "temperature": temperature,
        **get_litellm_params(model, user_info),
    }
    if max_tokens is not None:
        completion_params["max_tokens"] = max_tokens

    chat_template_kwargs = build_chat_template_kwargs(model.provider)
    if chat_template_kwargs:
        completion_params["chat_template_kwargs"] = chat_template_kwargs

    request_id = f"chatcmpl-{util.generate_id(k=16)}"
    started = util.get_now()

    try:
        response = await litellm.acompletion(**completion_params)
    except Exception as exc:
        await save_usage(
            user_info=user_info,
            model_name=model_name,
            model_type=ModelType.TEXT_GENERATION,
            request_id=request_id,
            latency_ms=int((util.get_now() - started).total_seconds() * 1000),
            status=ModelUsageStatus.ERROR,
            error_message=str(exc)[:1024],
            metadata={"stream": False, "source": usage_source},
            db_manager=db_manager,
        )
        raise

    input_tokens, cached_input_tokens, output_tokens, reasoning_tokens = extract_token_counts(
        getattr(response, "usage", None)
    )

    await save_usage(
        user_info=user_info,
        model_name=model_name,
        model_type=ModelType.TEXT_GENERATION,
        usage=TextGenerationUsage(
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        ),
        request_id=request_id,
        latency_ms=int((util.get_now() - started).total_seconds() * 1000),
        metadata={"stream": False, "source": usage_source},
        db_manager=db_manager,
    )

    # 추론(thinking) 텍스트는 최종 답변이 아니므로 버린다. 의도 분류·요약 결과를 그대로
    # 파싱하는 호출자들이 Gemma 4의 사고 과정까지 값으로 받아들이면 안 된다.
    content = ""
    if getattr(response, "choices", None):
        content = getattr(response.choices[0].message, "content", None) or ""
    return content.strip()


def build_system_prompt(
    language: Optional[Language],
    summary: Optional[str],
    sources: list[FaqSearchResult],
    intent: ChatIntent,
    regulation_sources: Optional[list[RegulationSearchResult]] = None,
    has_attachments: bool = False,
) -> str:
    """최종 응답 생성을 위한 시스템 프롬프트를 구성합니다.

    검색 근거·세션 요약을 함께 넣고, 근거 없이 지어내지 말 것과 출처를 밝힐 것을 명시합니다.
    (KAI-REQ-016 문맥 유지 / KAI-REQ-029 다국어 / KAI-REQ-040 미응답 처리)

    Args:
        language (Optional[Language]): 응답 언어
        summary (Optional[str]): 세션 누적 요약
        sources (list[FaqSearchResult]): FAQ 검색 결과
        intent (ChatIntent): 감지 의도
        regulation_sources (Optional[list[RegulationSearchResult]]): 학칙·규정 검색 결과
        has_attachments (bool): 이번 턴에 사용자 첨부가 있는지 여부

    Returns:
        str: 시스템 프롬프트
    """

    language_label = _language_label(language)

    # 규칙은 번호 없이 담고 마지막에 한 번에 번호를 붙입니다. 의도·첨부 조건이 겹칠 때
    # 번호를 손으로 적으면 같은 번호가 두 번 나가게 됩니다.
    rules = [
        f"반드시 {language_label}로 답변합니다.",
        "아래 [검색 근거]에 있는 내용만 사실로 사용합니다. 근거에 없는 학사 정보·일정·금액·연락처를 지어내지 마세요.",
        "근거를 사용해 답변할 때는 답변 끝에 어떤 FAQ·문서를 참고했는지 출처를 밝힙니다. (원문 URL이 있으면 함께 표기)",
        "근거가 부족하거나 확신할 수 없으면 추측하지 말고, 모른다고 명확히 안내한 뒤 학사지원팀 등 담당 부서 문의를 권합니다.",
        "학번·성적·수강 이력 등 개인 데이터는 조회 권한이 없으므로 절대 만들어내지 않습니다.",
        "불필요한 서론 없이 핵심부터 간결하게 안내하고, 항목이 여러 개면 목록으로 정리합니다. "
        "단, 감정에 공감하는 대목에는 이 간결함을 적용하지 않습니다.",
        # 정서 지원은 의도가 emotion이 아닐 때도 필요합니다. 학사 질문 끝에 "너무 힘드네요"가
        # 붙는 경우가 실제로 많은데, 정보만 답하고 지나가면 사용자는 무시당했다고 느낍니다.
        "사용자가 힘들다·슬프다·불안하다처럼 감정을 드러내면, 정보를 안내하기 전에 그 마음부터 알아줍니다. "
        "사용자가 말한 상황을 그대로 짚어 공감하고, 그다음에 필요한 정보를 이어 갑니다. "
        "이 공감은 [검색 근거]가 없어도 됩니다. 규칙 2·4는 학사 사실 정보에만 적용됩니다.",
    ]

    career_links = _external_links_text() if intent == ChatIntent.CAREER else ""
    counseling = (app_config.chatbot.counseling_contact or "").strip()

    if intent == ChatIntent.EMOTION:
        rules.append(_emotion_rule(counseling))
    elif intent == ChatIntent.SMALL_TALK:
        rules.append(
            "일상 대화에는 짧고 친절하게 답한 뒤, 학사·취업 관련 질문을 도울 수 있음을 안내합니다. "
            "다만 사용자가 감정을 털어놓았다면 안내를 서두르지 말고 공감을 먼저 충분히 합니다."
        )
    elif intent == ChatIntent.CAREER:
        # 취업 지원은 서비스의 핵심 목적(KAI-REQ-002/018/020/021)인데, 지식베이스에는 교내 행정
        # 규정만 있어 취업 질문은 근거가 거의 잡히지 않습니다. 규칙 2·4를 그대로 적용하면 모든
        # 취업 질문이 "모르겠습니다"로 끝나므로, 대학 고유 사실과 일반 지식을 구분해 허용합니다.
        rule = (
            "취업·진로 질문은 [검색 근거]에 답이 없어도, 이력서·자기소개서 작성법이나 면접 준비처럼 "
            "일반적으로 통용되는 내용은 아는 만큼 구체적으로 안내합니다. "
            "단, 계명대 고유의 프로그램명·모집 일정·지원 금액·담당 연락처는 근거 없이 단정하지 마세요."
        )
        if career_links:
            rule += " 대학 고유 정보는 아래 [외부 서비스 바로가기]에서 확인하도록 안내합니다."
        rules.append(rule)

    if has_attachments or intent == ChatIntent.DOCUMENT:
        rules.append(
            "사용자가 이번 질문에 첨부한 이미지·문서 내용을 우선 근거로 사용합니다. "
            "첨부에서 확인되는 내용만 사실로 답하고, 첨부·검색 근거에 없는 학사 정보는 지어내지 마세요. "
            "첨부만으로 답할 때는 FAQ 출처 표시가 없어도 됩니다."
        )

    parts = [
        "당신은 계명대학교 학생을 돕는 학사·취업 안내 챗봇입니다.",
        "학생이 어려움을 털어놓으면 정보를 주기 전에 먼저 마음을 헤아리는 따뜻한 태도를 지킵니다.",
        f"현재 시각은 {util.format_datetime(util.get_now())} 입니다.",
        "",
        "[답변 규칙]",
    ]
    parts += [f"{number}. {rule}" for number, rule in enumerate(rules, start=1)]

    if counseling:
        parts += ["", "[상담 안내]", counseling]

    if summary:
        parts += ["", "[이전 대화 요약]", summary.strip()]

    parts += ["", "[검색 근거]"]
    parts += _build_evidence_blocks(sources, regulation_sources or [])

    if career_links:
        parts += ["", "[외부 서비스 바로가기]", career_links]

    return "\n".join(parts)


def _emotion_rule(counseling: str) -> str:
    """정서 지원(EMOTION) 의도에 붙일 응답 규칙을 만듭니다.

    이 경로에는 검색 근거가 없습니다. 모델이 "근거가 없으니 모른다"로 빠지지 않도록,
    이번 발화가 정보 요청이 아니라는 것과 무엇을 해야 하는지를 순서로 지시합니다.

    상담 연결은 위기 신호가 보일 때만 하도록 조건을 붙입니다. 힘들다는 말마다 상담센터를
    안내하면 "그런 얘기는 여기 말고 저기 가서 하라"는 신호로 읽혀 대화가 끊깁니다.

    Args:
        counseling (str): 설정된 상담 안내 문구 (`chatbot.counseling_contact`)

    Returns:
        str: 시스템 프롬프트에 넣을 규칙 문장
    """

    rule = (
        "이번 발화는 정보 요청이 아니라 감정 표현입니다. 해결책을 먼저 내놓지 말고 다음 순서로 답합니다. "
        "(1) 사용자가 말한 상황과 감정을 구체적으로 되짚으며 공감합니다. "
        "\"많이 힘드시겠어요\" 한 줄로 끝내지 말고, 무엇 때문에 힘든지를 사용자의 말에서 그대로 짚어 주세요. "
        "의예과·학점·시험처럼 언급된 구체적 맥락도 함께 짚어 주세요. 공감만 2~4문장으로 충분히 합니다. "
        "(2) 그렇게 느끼는 것이 당연하다고 인정합니다. 훈계·비교·\"힘내세요\" 같은 상투적인 마무리, "
        "묻지 않은 조언은 하지 않습니다. 1~2문장으로 따뜻하게 인정합니다. "
        "(3) 더 이야기하고 싶은지, 어떤 부분이 가장 버거운지 부드럽게 물으며 대화를 열어 둡니다. "
        "학사·취업 안내는 사용자가 원할 때만, 마지막에 짧게 덧붙입니다. "
        "전체 6~10문장으로, 사무적인 안내문이 아니라 사람이 건네는 말투로 씁니다. "
        "짧게 끊지 말고 공감과 인정에 충분한 분량을 들이세요."
    )
    if counseling:
        rule += (
            " 다만 자해·자살 암시나 일상생활이 어려울 정도의 고통이 보이면, 공감한 뒤 혼자 감당하지 않도록 "
            "아래 [상담 안내]의 연락처를 함께 알립니다. 진단하거나 치료 방법을 제안하지는 마세요."
        )
    return rule


def _build_evidence_blocks(
    sources: list[FaqSearchResult],
    regulation_sources: list[RegulationSearchResult],
) -> list[str]:
    """[검색 근거] 블록을 길이 예산 안에서 구성합니다.

    규정 검색은 유사도 임계값 없이 상위 12건을 돌려주고 청크 하나가 9천 자를 넘기도 합니다.
    전부 이어 붙이면 프롬프트가 모델 컨텍스트 상한을 넘겨 응답 생성이 400으로 실패하므로,
    상위 순위부터 예산이 허용하는 만큼만 담고 잘라낸 사실을 모델에 알립니다.

    Args:
        sources (list[FaqSearchResult]): FAQ 검색 결과
        regulation_sources (list[RegulationSearchResult]): 학칙·규정 검색 결과

    Returns:
        list[str]: 프롬프트에 이어 붙일 근거 블록 목록
    """

    if not sources and not regulation_sources:
        return ["(검색된 근거가 없습니다. 확실하지 않은 학사 정보는 답변하지 마세요.)"]

    budget = app_config.chatbot.evidence_max_chars
    content_limit = app_config.chatbot.source_content_max_chars
    blocks: list[str] = []
    used = 0
    dropped = 0
    index = 0

    def append(block: str, header: Optional[str] = None) -> bool:
        """예산이 남아 있으면 블록을 담고, 담았는지 여부를 반환합니다.

        1순위 근거는 예산을 넘겨도 담습니다. 근거를 하나도 넣지 않으면 모델이 아무 근거 없이
        학사 정보를 답하게 되어, 길이를 아끼려다 더 큰 문제를 만들기 때문입니다.
        """

        nonlocal used
        cost = len(block) + (len(header) if header else 0)
        if budget > 0 and blocks and used + cost > budget:
            return False
        if header:
            blocks.append(header)
        blocks.append(block)
        used += cost
        return True

    for position, source in enumerate(sources):
        index += 1
        block = [f"{index}. (유사도 {source.score:.3f}) 질문: {source.question}"]
        if source.answer:
            block.append(f"   답변: {_truncate(source.answer, content_limit)}")
        if source.category_code:
            block.append(f"   카테고리: {source.category_code}")
        if source.department_code:
            block.append(f"   담당 부서: {source.department_code}")
        if source.source_url:
            block.append(f"   원문: {source.source_url}")
        if not append("\n".join(block), "- FAQ" if position == 0 else None):
            dropped += len(sources) - position
            break

    for position, source in enumerate(regulation_sources):
        index += 1
        block = [f"{index}. (관련도 {source.score:.3f}) 출처: {_regulation_label(source)}"]
        if source.effective_date:
            # 파일명 시행일과 본문 개정일을 혼동하지 않도록 어느 날짜인지 명시합니다.
            block.append(f"   현행 시행일(파일명 기준): {source.effective_date}")
        block.append(f"   원문: {_truncate(source.content, content_limit)}")
        if not append("\n".join(block), "- 학칙·규정 원문" if position == 0 else None):
            dropped += len(regulation_sources) - position
            break

    if dropped > 0:
        blocks.append(f"(길이 제한으로 관련도가 낮은 근거 {dropped}건은 생략했습니다.)")

    return blocks


def build_source_payload(
    results: list[FaqSearchResult],
    regulation_results: Optional[list[RegulationSearchResult]] = None,
) -> list[dict]:
    """검색 결과를 `chat_messages.sources` 저장 형식으로 변환합니다.

    FAQ와 학칙·규정은 원천이 달라 `source_type`으로 구분합니다.
    프론트엔드가 근거를 표시할 때 어느 쪽을 인용했는지 알아야 하기 때문입니다.

    Args:
        results (list[FaqSearchResult]): FAQ 검색 결과
        regulation_results (Optional[list[RegulationSearchResult]]): 학칙·규정 검색 결과

    Returns:
        list[dict]: 응답 근거 목록
    """

    sources = [
        {
            "source_type": SourceType.FAQ.value,
            "source_id": str(result.faq_id),
            "question": result.question,
            "category_code": result.category_code,
            "department_code": result.department_code,
            "source_url": result.source_url,
            "score": round(result.score, 4),
        }
        for result in results
    ]

    for result in regulation_results or []:
        sources.append({
            "source_type": SourceType.REGULATION.value,
            "source_id": str(result.document_id),
            "question": _regulation_label(result),
            "doc_id": result.doc_id,
            "article": result.article,
            "section_type": result.section_type,
            "effective_date": result.effective_date,
            "file_name": result.file_name,
            "score": round(result.score, 4),
        })

    return sources


def _regulation_label(result: RegulationSearchResult) -> str:
    """규정 근거를 사람이 읽을 수 있는 한 줄 라벨로 만듭니다.

    Args:
        result (RegulationSearchResult): 규정 검색 결과

    Returns:
        str: 출처 표기용 라벨 (예: "학칙 제15조")
    """

    title = result.file_name.rsplit(".", 1)[0]
    return f"{title} {result.article}".strip() if result.article else title


# ===== Nodes =====

async def condense_query(state: ChatGraphState, config: Optional[RunnableConfig] = None) -> dict:
    """직전 대화를 반영해 질문을 독립적인 형태로 재작성합니다. (KAI-REQ-015)

    "휴학은 어떻게 신청하나요?" 다음에 "그럼 기간은 얼마나 되나요?"가 오면, 뒤 문장만으로는
    무엇의 기간인지 알 수 없어 의도 분류도 검색도 실패합니다. 지식베이스 검색 전에 대화 맥락을
    질문 자체에 복원해 넣어야 합니다.

    첫 턴에는 재작성할 맥락이 없으므로 LLM을 호출하지 않습니다.
    재작성에 실패하면 원문으로 검색을 계속합니다. (맥락을 못 살릴 뿐 동작은 유지)
    """

    deps = _deps(config)
    query = state.get("query") or ""
    history = state.get("history") or []

    if not query or not history:
        return {"search_query": query, "query_condensed": False}

    recent = [
        item for item in history[-CONDENSE_HISTORY_LIMIT:]
        if item.get("role") in ("user", "assistant") and item.get("content")
    ]
    if not recent:
        return {"search_query": query, "query_condensed": False}

    # 재작성은 지시어를 실제 대상으로 바꾸는 작업이라 발화의 앞부분만 있으면 충분합니다.
    # 이력서 전문처럼 긴 발화를 그대로 넣으면 컨텍스트 상한을 넘겨 호출 자체가 실패합니다.
    messages: list[dict] = [{"role": "system", "content": CONDENSE_SYSTEM_PROMPT}]
    messages += [
        {"role": item["role"], "content": _truncate(item["content"], CONDENSE_HISTORY_ITEM_MAX_CHARS)}
        for item in recent
    ]
    messages.append({"role": "user", "content": _truncate(query, CONDENSE_QUERY_MAX_CHARS)})

    try:
        rewritten = await complete_text(
            model_name=app_config.chatbot.text_model,
            messages=messages,
            user_info=deps.user_info,
            db_manager=deps.db_manager,
            max_tokens=CONDENSE_MAX_TOKENS,
            usage_source="chatbot_condense",
        )
    except Exception:
        _log(deps).warning("질문 재작성 모델을 사용할 수 없어 원문으로 검색합니다.", exc_info=True)
        return {"search_query": query, "query_condensed": False}

    rewritten = (rewritten or "").strip().strip('"').strip()
    # 모델이 빈 문자열이나 설명 문단을 뱉는 경우가 있어 원문으로 되돌립니다.
    # 재작성 결과는 한 문장짜리 질문이어야 하므로 지나치게 길면 신뢰하지 않습니다.
    if not rewritten or len(rewritten) > len(query) + 200:
        return {"search_query": query, "query_condensed": False}

    if rewritten != query:
        _log(deps).debug(f"후속 질문을 재작성했습니다. ({query!r} -> {rewritten!r})")

    return {"search_query": rewritten, "query_condensed": rewritten != query}


async def classify_intent(state: ChatGraphState, config: Optional[RunnableConfig] = None) -> dict:
    """사용자 발화의 의도와 응답 언어를 한 번의 호출로 판정합니다. (KAI-REQ-029/030/037/038)

    비속어 사전 필터를 먼저 적용하고, 걸리지 않으면 LLM으로 `의도|언어` 한 줄을 받습니다.
    모델을 사용할 수 없으면 되묻기 대신 검색을 시도하도록 `intent_error`를 세워 둡니다.
    (모델 장애를 "질문이 모호합니다"로 안내하면 사용자가 오해하기 때문입니다.)

    언어는 사용자가 UI에서 직접 고른 경우(`language_explicit`) 판정 결과로 덮어쓰지 않습니다.
    """

    deps = _deps(config)
    # 재작성된 질문으로 분류합니다. 후속 질문("그럼 기간은?")을 원문 그대로 분류하면
    # 맥락이 없어 unknown으로 떨어지고 되묻기로 빠집니다.
    query = state.get("search_query") or state.get("query") or ""
    # 언어는 사용자가 실제로 입력한 원문을 기준으로 판정해야 합니다. 재작성은 맥락을 채워 넣는
    # 과정이라 원문에 없던 한국어가 섞여 들어올 수 있습니다.
    original_query = state.get("query") or query

    def resolved(detected: Optional[Language]) -> dict:
        """감지 결과를 명시 지정 여부에 맞춰 상태 변경분으로 만듭니다."""

        detected = detected or guess_language_by_script(original_query)
        if state.get("language_explicit"):
            return {"detected_language": detected}
        return {"detected_language": detected, "language": detected}

    # 비속어는 사용자가 실제로 입력한 원문에서 걸러야 합니다. (재작성 과정에서 순화될 수 있음)
    keyword = match_abuse_keywords(original_query)
    if keyword:
        _log(deps).debug(f"비속어 사전 필터에 매칭되었습니다. (keyword={keyword})")
        # 사전 필터로 끝내면 LLM 판정이 없으므로 언어는 표기 문자로 추정합니다.
        return {"intent": ChatIntent.ABUSE, "intent_error": False, **resolved(None)}

    messages: list[dict] = [{"role": "system", "content": CLASSIFY_SYSTEM_PROMPT}]
    for sample_query, sample_label in CLASSIFY_FEW_SHOTS:
        messages.append({"role": "user", "content": sample_query})
        messages.append({"role": "assistant", "content": sample_label})
    messages.append({"role": "user", "content": _truncate(query, CLASSIFY_QUERY_MAX_CHARS)})

    try:
        content = await complete_text(
            model_name=app_config.chatbot.text_model,
            messages=messages,
            user_info=deps.user_info,
            db_manager=deps.db_manager,
            max_tokens=CLASSIFY_MAX_TOKENS,
            usage_source="chatbot_classify",
        )
    except Exception:
        _log(deps).warning("의도 분류 모델을 사용할 수 없어 검색 경로로 진행합니다.", exc_info=True)
        return {
            "intent": ChatIntent.UNKNOWN,
            "intent_error": True,
            "service_unavailable": True,
            **resolved(None),
        }

    intent, language = parse_intent_and_language(content)

    # 응답이 비었다는 것은 "모호한 발화"가 아니라 모델이 라벨을 내지 못했다는 뜻입니다.
    # (gemma-4-31b-it은 thinking을 끌 수 없어 상한이 빠듯하면 사고 과정만 내고 끝납니다)
    # 이때 되묻기로 빠지면 멀쩡한 질문에 "구체적으로 말해 달라"고 답하게 되므로 검색을 시도합니다.
    if not content.strip():
        _log(deps).warning("의도·언어 분류 응답이 비어 있어 검색 경로로 진행합니다.")
        return {"intent": ChatIntent.UNKNOWN, "intent_error": True, **resolved(None)}

    return {"intent": intent, "intent_error": False, **resolved(language)}

async def retrieve(state: ChatGraphState, config: Optional[RunnableConfig] = None) -> dict:
    """FAQ 지식베이스에서 근거를 검색합니다. (KAI-REQ-015)

    결과가 없으면 `NO_RESULT`, 임계값 미달이면 `LOW_SCORE`로 미응답 사유를 남깁니다.
    벡터 검색이나 임베딩 모델을 사용할 수 없는 경우에도 예외를 밖으로 던지지 않고
    `service_unavailable`을 세워 안내 문구로 우아하게 처리합니다.
    """

    deps = _deps(config)
    # 후속 질문은 원문 대신 재작성본으로 검색합니다. (condense_query 참고)
    search_query = state.get("search_query") or state.get("query") or ""

    # FAQ와 학칙·규정은 서로 독립된 지식베이스입니다. 한쪽이 준비되지 않았거나 실패해도
    # 나머지 한쪽으로 답변할 수 있어야 하므로, 각각 따로 시도하고 실패를 따로 기록합니다.
    results: list[FaqSearchResult] = []
    regulation_results: list[RegulationSearchResult] = []
    latency_ms = 0
    retrieval_errors: list[str] = []

    try:
        faq_top_k, faq_threshold = await _collection_retrieval_settings(
            deps.db_manager, FAQ_COLLECTION_NAME
        )
        # 언어 필터는 걸지 않습니다. FAQ 원문이 한국어라도 답변은 사용자 언어로 생성하기 때문입니다.
        results, faq_latency_ms = await search_faq(
            db_manager=deps.db_manager,
            user_info=deps.user_info,
            query_text=search_query,
            top_k=faq_top_k,
            score_threshold=faq_threshold,
        )
        latency_ms += faq_latency_ms
    except FaqKnowledgeBaseNotReady as exc:
        # 색인된 FAQ가 아직 없는 상태입니다. 장애가 아니므로 오류로 기록하지 않고
        # 규정 근거만으로 답변을 이어갑니다. (계명대 FAQ 원천 데이터 미제공 상태)
        _log(deps).debug(f"FAQ 검색을 건너뜁니다. ({exc})")
    except Exception as exc:
        _log(deps).warning(f"FAQ 검색을 수행할 수 없습니다. ({exc})", exc_info=True)
        retrieval_errors.append(f"FAQ: {exc}")

    # 학칙·규정은 별도 지식베이스(pgvector + hybrid)에 있습니다. FAQ에 없는 질문이라도
    # 규정 원문에는 답이 있는 경우가 많아, 두 근거를 함께 모아 답변에 씁니다. (KAI-REQ-013)
    try:
        reg_top_k, _reg_threshold = await _collection_retrieval_settings(
            deps.db_manager, REGULATION_COLLECTION_NAME
        )
        regulation_results, regulation_latency_ms = await search_regulations(
            db_manager=deps.db_manager,
            user_info=deps.user_info,
            query_text=search_query,
            top_k=reg_top_k,
        )
        latency_ms += regulation_latency_ms
    except Exception as exc:
        _log(deps).warning(f"학칙·규정 검색을 수행할 수 없습니다. ({exc})", exc_info=True)
        retrieval_errors.append(f"학칙·규정: {exc}")

    # 근거를 하나도 못 얻었는데 그 이유가 "결과 없음"이 아니라 "검색 자체를 못 했다"면
    # 근거 부족이 아니라 서비스 장애입니다. 이때만 안내 문구로 빠집니다.
    if not results and not regulation_results and retrieval_errors:
        return {
            "faq_results": [],
            "regulation_results": [],
            "sources": [],
            "retrieval_attempted": True,
            "retrieval_latency_ms": latency_ms,
            "retrieval_error": " / ".join(retrieval_errors)[:1024],
            "service_unavailable": True,
            "unanswered_reason": UnansweredReason.MODEL_ERROR,
        }

    if not results and not regulation_results:
        # search_faq가 score_threshold로 걸러내므로 임계값 미달도 빈 결과로 돌아옵니다.
        # 임계값을 낮춰 재검색해 "결과 없음"과 "유사도 미달"을 구분합니다.
        reason = UnansweredReason.NO_RESULT
        try:
            loose_results, _ = await search_faq(
                db_manager=deps.db_manager,
                user_info=deps.user_info,
                query_text=search_query,
                top_k=app_config.chatbot.top_k,
                score_threshold=None,
            )
            if loose_results:
                reason = UnansweredReason.LOW_SCORE
        except Exception:
            _log(deps).debug("미응답 사유 판별을 위한 재검색에 실패했습니다.", exc_info=True)

        return {
            "faq_results": [],
            "regulation_results": [],
            "sources": [],
            "retrieval_attempted": True,
            "retrieval_latency_ms": latency_ms,
            "unanswered_reason": reason,
        }

    return {
        "faq_results": results,
        "regulation_results": regulation_results,
        "sources": build_source_payload(results, regulation_results),
        "retrieval_attempted": True,
        "retrieval_latency_ms": latency_ms,
        "unanswered_reason": None,
    }

async def generate(state: ChatGraphState, config: Optional[RunnableConfig] = None) -> dict:
    """최종 응답 생성을 위한 메시지를 구성합니다.

    실제 텍스트 스트리밍은 엔드포인트가 `messages`를 받아 `stream_chat_completion`으로 수행합니다.
    (SSE 스트리밍을 위해 async generator를 그래프 밖으로 흘리지 않기 위한 구조입니다.)
    """

    _deps(config)

    attachments = state.get("attachments") or []
    system_prompt = build_system_prompt(
        language=state.get("language"),
        summary=state.get("summary"),
        sources=state.get("faq_results") or [],
        regulation_sources=state.get("regulation_results") or [],
        intent=state.get("intent") or ChatIntent.UNKNOWN,
        has_attachments=bool(attachments),
    )

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    messages += _budgeted_history(state.get("history") or [])
    messages.append({
        "role": "user",
        "content": build_user_content(
            query=state.get("query") or "",
            resolved=attachments,
            query_max_chars=app_config.chatbot.query_max_chars,
        ),
    })

    return {"messages": messages, "answer": None, "needs_generation": True}


def _budgeted_history(history: list[dict]) -> list[dict]:
    """대화 이력을 길이 예산 안에서 최근 것부터 담습니다.

    이력서 전문처럼 긴 발화가 이력에 남으면 다음 턴부터 프롬프트가 컨텍스트 상한을 넘깁니다.
    최근 대화가 문맥 유지(KAI-REQ-016)에 더 중요하므로 뒤에서부터 채우고, 예산을 넘기면
    오래된 메시지를 버립니다.

    Args:
        history (list[dict]): 최근 대화 이력

    Returns:
        list[dict]: 예산에 맞게 추린 이력 메시지 (시간순)
    """

    budget = app_config.chatbot.history_max_chars
    selected: list[dict] = []
    used = 0

    for item in reversed(history):
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not content:
            continue

        if budget > 0:
            remaining = budget - used
            if remaining <= 0:
                break
            # 예산을 넘긴 메시지는 버리지 않고 남은 예산만큼만 담습니다. 직전 발화가 길다는
            # 이유로 통째로 빠지면 후속 질문의 지시어를 해석할 수 없게 됩니다.
            content = _truncate(content, remaining)

        selected.append({"role": role, "content": content})
        used += len(content)

    selected.reverse()
    return selected

async def handle_attachment_required(state: ChatGraphState, config: Optional[RunnableConfig] = None) -> dict:
    """첨부 내용 문의인데 파일이 없을 때 안내 문구를 반환합니다."""

    _deps(config)
    return {
        "answer": localized_message("attachment_required", state.get("language")),
        "needs_generation": False,
        "sources": [],
        "unanswered_reason": UnansweredReason.AMBIGUOUS,
    }


async def handle_abuse(state: ChatGraphState, config: Optional[RunnableConfig] = None) -> dict:
    """비속어·목적 외 발화에 정해진 안내 문구를 반환합니다. (KAI-REQ-038)"""

    _deps(config)
    return {
        "answer": localized_message("abuse", state.get("language")),
        "needs_generation": False,
        "sources": [],
        "unanswered_reason": None,
    }

async def handle_ambiguous(state: ChatGraphState, config: Optional[RunnableConfig] = None) -> dict:
    """모호한 질문에 되묻기 문구를 반환합니다. (KAI-REQ-037)"""

    _deps(config)
    return {
        "answer": localized_message("ambiguous", state.get("language")),
        "needs_generation": False,
        "sources": [],
        "unanswered_reason": UnansweredReason.AMBIGUOUS,
    }

async def handle_fallback(state: ChatGraphState, config: Optional[RunnableConfig] = None) -> dict:
    """답변할 수 없는 질문에 양해 문구를 반환합니다. (KAI-REQ-040)"""

    _deps(config)
    return {
        "answer": localized_message("fallback", state.get("language")),
        "needs_generation": False,
        "sources": [],
        "unanswered_reason": state.get("unanswered_reason") or UnansweredReason.NO_RESULT,
    }

async def handle_personal(state: ChatGraphState, config: Optional[RunnableConfig] = None) -> dict:
    """개인 데이터 문의에 미제공 안내와 바로가기를 반환합니다. (KAI-REQ-003~012/035)

    학내 개인정보 API가 아직 제공되지 않으므로 절대 가짜 개인 데이터를 만들지 않고,
    안내 문구와 외부 바로가기만 제시합니다.
    """

    _deps(config)

    language = state.get("language")
    answer = localized_message("personal_data_unavailable", language)
    links = _external_links_text()
    if links:
        answer = f"{answer}\n\n{_use_links_text(language)}\n{links}"

    return {
        "answer": answer,
        "needs_generation": False,
        "sources": [],
        "unanswered_reason": UnansweredReason.OUT_OF_SCOPE,
    }

async def summarize(state: ChatGraphState, config: Optional[RunnableConfig] = None) -> dict:
    """세션 메시지가 기준을 넘으면 누적 요약을 갱신합니다. (KAI-REQ-041)

    정해진 문구로 응답이 확정된 경로에서만 그래프가 직접 요약을 갱신합니다.
    LLM 생성 경로는 응답이 스트리밍으로 완성된 뒤 엔드포인트가 `update_session_summary`를 호출합니다.
    """

    deps = _deps(config)

    if state.get("needs_generation"):
        return {"summary_updated": None}

    answer = state.get("answer")
    if not answer:
        return {"summary_updated": None}

    summary = await update_session_summary(
        db_manager=deps.db_manager,
        user_info=deps.user_info,
        previous_summary=state.get("summary"),
        history=state.get("history") or [],
        query=state.get("query") or "",
        answer=answer,
        message_count=state.get("message_count") or 0,
        logger=deps.logger,
    )
    return {"summary_updated": summary}


# ===== Routing =====

def route_by_intent(state: ChatGraphState) -> str:
    """의도에 따라 다음 노드를 결정합니다."""

    intent = state.get("intent") or ChatIntent.UNKNOWN
    has_attachments = bool(state.get("attachments"))

    # 정서 지원은 지식베이스가 아니라 공감으로 답하는 경로입니다. 검색을 태우면 근거가 잡히지
    # 않아 미응답 문구로 끊기고, 힘들다고 말한 학생이 "정보를 찾을 수 없다"는 답을 받게 됩니다.
    # 어떤 분기보다 먼저 두어, 감정 표현이 정형 문구 경로로 새지 않도록 합니다.
    if intent == ChatIntent.EMOTION:
        return "generate"
    if intent == ChatIntent.ABUSE:
        return "handle_abuse"
    if intent == ChatIntent.PERSONAL:
        return "handle_personal"
    # 첨부가 있으면 KB 검색 없이 첨부 내용을 근거로 생성합니다.
    if has_attachments:
        return "generate"
    if intent == ChatIntent.DOCUMENT:
        return "handle_attachment_required"
    if intent == ChatIntent.SMALL_TALK:
        return "generate"
    if intent in RETRIEVAL_INTENTS:
        return "retrieve"

    # 모델 장애로 분류에 실패한 경우에는 되묻지 않고 검색을 시도합니다.
    if state.get("intent_error"):
        return "retrieve"
    return "handle_ambiguous"

def route_after_retrieve(state: ChatGraphState) -> str:
    """검색 결과 유무에 따라 다음 노드를 결정합니다.

    FAQ와 학칙·규정은 서로 독립된 지식베이스라 어느 한쪽만 걸려도 답변할 근거가 됩니다.
    FAQ만 보고 분기하면 규정 근거를 찾아 놓고도 버리게 되므로 둘 다 확인합니다.
    (FAQ가 0건인 현재 상태에서는 규정 근거가 전부입니다)
    """

    if state.get("faq_results") or state.get("regulation_results"):
        return "generate"

    # 취업 지원은 서비스의 핵심 목적(KAI-REQ-002/018/020/021)인데 지식베이스에는 교내 행정 규정만
    # 있어 취업 질문은 근거가 잡히지 않습니다. 여기서 정형 문구로 끊으면 취업 질문에 영구히 답할 수
    # 없으므로, 일반적인 취업 안내와 외부 바로가기로 답변을 이어갑니다.
    # 근거 없는 응답인 것은 사실이므로 `unanswered_reason`은 지우지 않고 남겨, 관리자가
    # 미응답 질문 목록(KAI-REQ-031)에서 보강할 FAQ를 찾을 수 있게 합니다.
    if (state.get("intent") or ChatIntent.UNKNOWN) == ChatIntent.CAREER:
        return "generate"

    return "handle_fallback"


def build_chat_graph():
    """챗봇 오케스트레이션 그래프를 구성하고 컴파일합니다.

    Returns:
        컴파일된 LangGraph 그래프
    """

    graph = StateGraph(ChatGraphState)

    graph.add_node("condense_query", condense_query)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_node("handle_abuse", handle_abuse)
    graph.add_node("handle_ambiguous", handle_ambiguous)
    graph.add_node("handle_fallback", handle_fallback)
    graph.add_node("handle_personal", handle_personal)
    graph.add_node("handle_attachment_required", handle_attachment_required)
    graph.add_node("summarize", summarize)

    # 의도 분류·검색 모두 대화 맥락이 복원된 질문을 써야 하므로 재작성을 가장 앞에 둡니다.
    graph.add_edge(START, "condense_query")
    graph.add_edge("condense_query", "classify_intent")
    graph.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {
            "retrieve": "retrieve",
            "generate": "generate",
            "handle_abuse": "handle_abuse",
            "handle_ambiguous": "handle_ambiguous",
            "handle_personal": "handle_personal",
            "handle_attachment_required": "handle_attachment_required",
        },
    )
    graph.add_conditional_edges(
        "retrieve",
        route_after_retrieve,
        {
            "generate": "generate",
            "handle_fallback": "handle_fallback",
        },
    )
    graph.add_edge("generate", "summarize")
    graph.add_edge("handle_abuse", "summarize")
    graph.add_edge("handle_ambiguous", "summarize")
    graph.add_edge("handle_fallback", "summarize")
    graph.add_edge("handle_personal", "summarize")
    graph.add_edge("handle_attachment_required", "summarize")
    graph.add_edge("summarize", END)

    return graph.compile()


chat_graph = build_chat_graph()
"""모듈 로드 시 1회 컴파일한 챗봇 그래프 (요청마다 재사용)"""


async def run_chat_graph(
    query: str,
    session_id: str,
    message_id: str,
    language: Optional[Language],
    history: list[dict],
    summary: Optional[str],
    message_count: int,
    db_manager: DatabaseManager,
    user_info: TokenUserInfo,
    logger: Optional[Logger] = None,
    attachments: Optional[list] = None,
) -> ChatGraphState:
    """챗봇 그래프를 실행하고 최종 상태를 반환합니다.

    Args:
        query (str): 사용자 질문
        session_id (str): 세션 ID
        message_id (str): 사용자 메시지 ID
        language (Optional[Language]): 명시 지정된 응답 언어. None이면 발화에서 자동 감지합니다.
        history (list[dict]): 최근 대화 이력
        summary (Optional[str]): 세션 누적 요약
        message_count (int): 세션 메시지 수
        db_manager (DatabaseManager): 데이터베이스 매니저
        user_info (TokenUserInfo): 사용자 정보
        logger (Optional[Logger]): 로거 (Default: None)
        attachments (Optional[list]): 해석된 첨부 목록 (`ResolvedAttachment`)

    Returns:
        ChatGraphState: 그래프 실행 결과 상태
    """

    deps = ChatGraphDeps(
        db_manager=db_manager,
        user_info=user_info,
        logger=logger,
    )

    initial_state: ChatGraphState = {
        "query": query,
        # condense_query가 대화 맥락을 반영해 덮어씁니다. (첫 턴이면 원문 그대로)
        "search_query": query,
        "query_condensed": False,
        "session_id": session_id,
        "message_id": message_id,
        # 자동이면 일단 서비스 기본값으로 두고 classify_intent가 감지 결과로 덮어씁니다.
        # (분류 호출이 통째로 실패해도 language 키가 비어 있지 않도록 하기 위함)
        "language": language or Language.KO,
        "language_explicit": language is not None,
        "detected_language": None,
        "history": history or [],
        "attachments": attachments or [],
        "summary": summary,
        "message_count": message_count,
        "intent": ChatIntent.UNKNOWN,
        "intent_error": False,
        "faq_results": [],
        "sources": [],
        "messages": [],
        "answer": None,
        "needs_generation": False,
        "unanswered_reason": None,
        "retrieval_attempted": False,
        "retrieval_latency_ms": 0,
        "retrieval_error": None,
        "service_unavailable": False,
        "summary_updated": None,
    }

    return await chat_graph.ainvoke(initial_state, config={"configurable": {"deps": deps}})


async def update_session_summary(
    db_manager: DatabaseManager,
    user_info: TokenUserInfo,
    previous_summary: Optional[str],
    history: list[dict],
    query: str,
    answer: str,
    message_count: int,
    logger: Optional[Logger] = None,
) -> Optional[str]:
    """세션 누적 요약을 갱신합니다. (KAI-REQ-041 세션 기억)

    메시지 수가 `app_config.chatbot.summary_trigger_count`를 넘은 세션만 갱신하며,
    모델을 사용할 수 없으면 조용히 건너뜁니다. (요약 실패로 대화가 끊기면 안 되기 때문)

    Args:
        db_manager (DatabaseManager): 데이터베이스 매니저
        user_info (TokenUserInfo): 사용자 정보
        previous_summary (Optional[str]): 이전 요약
        history (list[dict]): 최근 대화 이력
        query (str): 이번 사용자 질문
        answer (str): 이번 챗봇 답변
        message_count (int): 세션 메시지 수
        logger (Optional[Logger]): 로거 (Default: None)

    Returns:
        Optional[str]: 갱신된 요약 (갱신하지 않았으면 None)
    """

    log = logger or globals()["logger"]

    if message_count < app_config.chatbot.summary_trigger_count:
        return None

    conversation = []
    for item in history or []:
        role = item.get("role")
        content = item.get("content")
        if role and content:
            conversation.append(f"{'사용자' if role == 'user' else '챗봇'}: {content}")
    conversation.append(f"사용자: {query}")
    conversation.append(f"챗봇: {answer}")

    user_content = ""
    if previous_summary:
        user_content += f"[기존 요약]\n{previous_summary.strip()}\n\n"
    # 최근 대화가 요약에 더 중요하므로 예산을 넘기면 앞(오래된) 쪽을 버립니다.
    conversation_text = "\n".join(conversation)
    if len(conversation_text) > SUMMARY_INPUT_MAX_CHARS:
        conversation_text = "(앞부분 생략)\n" + conversation_text[-SUMMARY_INPUT_MAX_CHARS:]
    user_content += "[최근 대화]\n" + conversation_text

    messages = [
        {
            "role": "system",
            "content": (
                "당신은 계명대학교 챗봇의 대화 요약기입니다.\n"
                "기존 요약과 최근 대화를 합쳐 하나의 누적 요약으로 갱신하세요.\n"
                "- 사용자가 무엇을 묻고 어떤 안내를 받았는지 중심으로 정리합니다.\n"
                "- 학번·성적 등 개인 식별 정보는 요약에 남기지 않습니다.\n"
                "- 500자 이내의 한국어 평문으로만 출력하고 다른 말을 덧붙이지 마세요."
            ),
        },
        {"role": "user", "content": user_content},
    ]

    try:
        summary = await complete_text(
            model_name=app_config.chatbot.text_model,
            messages=messages,
            user_info=user_info,
            db_manager=db_manager,
            max_tokens=SUMMARY_MAX_TOKENS,
            temperature=0.2,
            usage_source="chatbot_summarize",
        )
    except Exception:
        log.warning("세션 요약을 갱신하지 못했습니다.", exc_info=True)
        return None

    return summary or None


def get_retrieval_collection_name() -> str:
    """검색 로그(`retrieval_logs.collection_name`)에 기록할 지식베이스 이름을 반환합니다. (KAI-REQ-043)

    벡터 저장소 구현이 바뀌어도 로그 이름은 `faq_service`가 정한 값을 따라갑니다.
    (상수명이 아직 정리 중이라 없으면 기본값을 사용합니다.)

    Returns:
        str: FAQ 지식베이스 이름
    """

    return getattr(faq_service, "FAQ_COLLECTION_NAME", "kmu_faq_knowledge")
