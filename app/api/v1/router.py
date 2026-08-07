from fastapi import APIRouter

from .endpoints import (
    api_key_router,
    dashboard_router,
    model_router,
    model_use_router,
    collection_router,
    document_router,
    graphrag_router,
    prompt_router,
    faq_router,
    chat_router,
    chatbot_admin_router,
    ingestion_router,
    rag_router,
    masking_router,
)


api_v1_router = APIRouter()
api_v1_router.include_router(api_key_router, prefix="/api_key", tags=["API Key 관리"])
api_v1_router.include_router(dashboard_router, prefix="/dashboard", tags=["대시보드"])
api_v1_router.include_router(model_router, prefix="/model", tags=["모델 관리"])
api_v1_router.include_router(model_use_router, prefix="", tags=["모델 사용"])
api_v1_router.include_router(collection_router, prefix="/collection", tags=["컬렉션 관리"])
api_v1_router.include_router(document_router, prefix="/document", tags=["문서 관리"])
api_v1_router.include_router(graphrag_router, prefix="/graphrag", tags=["지식 그래프"])
api_v1_router.include_router(prompt_router, prefix="/prompt", tags=["프롬프트 관리"])
api_v1_router.include_router(faq_router, prefix="/faq", tags=["FAQ 지식베이스"])
# 챗봇 대화·분석은 /chatbot 아래로 모은다.
# /chat 을 쓰면 model_use의 OpenAI 호환 엔드포인트(/v1/chat/completions)와 네임스페이스가 섞인다.
api_v1_router.include_router(chat_router, prefix="/chatbot", tags=["챗봇 대화"])
api_v1_router.include_router(chatbot_admin_router, prefix="/chatbot", tags=["챗봇 분석·로그"])
api_v1_router.include_router(ingestion_router, prefix="/ingestion", tags=["데이터 수집"])
api_v1_router.include_router(rag_router, prefix="/rag", tags=["RAG 관리"])
api_v1_router.include_router(masking_router, prefix="/masking", tags=["마스킹 규칙"])
