from .api_key import router as api_key_router
from .dashboard import router as dashboard_router
from .model import router as model_router
from .model_use import router as model_use_router
from .collection import router as collection_router
from .document import router as document_router
from .graphrag import router as graphrag_router
from .prompt import router as prompt_router
from .faq import router as faq_router
from .chat import router as chat_router
from .chatbot_admin import router as chatbot_admin_router
from .ingestion import router as ingestion_router
from .rag import router as rag_router
from .masking import router as masking_router


__all__ = [
    "api_key_router",
    "dashboard_router",
    "model_router",
    "model_use_router",
    "collection_router",
    "document_router",
    "graphrag_router",
    "prompt_router",
    "faq_router",
    "chat_router",
    "chatbot_admin_router",
    "ingestion_router",
    "rag_router",
    "masking_router",
]
