import logging
logging.getLogger("LiteLLM").setLevel(logging.WARNING)

import litellm
litellm.suppress_debug_info = True
litellm.drop_params = True

import time

from logging import Logger

from typing import Any, Optional, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import StreamingResponse

from litellm import ModelResponse, EmbeddingResponse
from litellm.utils import CustomStreamWrapper

from app.exceptions.api_exception import (
    DEFAULT_EXCEPTION_RESPONSES,
    DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
)
from app.payloads.v1.model_use import (
    ChatMessageRole,
    ChatCompletionsPayload,
    EmbeddingsPayload,
    RerankPayload,
)
from app.responses.exception import (
    BadRequestResponse,
    NotFoundResponse,
    InternalServerErrorResponse,
)
from app.responses.v1.model_use import (
    ModelInfo,
    ModelsListResponse,
    ChatCompletionMessage,
    ChatCompletionChoice,
    UsageInfo,
    ChatCompletionResponse,
    ChatCompletionStreamDelta,
    ChatCompletionStreamChoice,
    ChatCompletionStreamResponse,
    EmbeddingData,
    EmbeddingUsage,
    EmbeddingsResponse,
    RerankResult,
    RerankResponse,
)
from app.models.auth import TokenUserInfo
from app.models.api.exception import (
    BadRequestError,
	NotFoundError,
)
from app.models.enum import (
    ModelProvider,
    ModelType,
    ModelStatus,
    ModelUsageStatus,
)
from app.models.cost import (
    TextGenerationUsage as TextGenerationUsageModel,
    EmbeddingUsage as EmbeddingUsageModel,
    RerankUsage as RerankUsageModel,
)
from app.utils.litellm import build_chat_template_kwargs, create_embedding, extract_token_counts, get_litellm_model_name, get_litellm_params, save_usage
from app.utils.logger import get_api_logger
from app.utils.database import DatabaseManager, get_db_manager
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.auth as auth
import app.utils.common as util


router = APIRouter()


def _drop_none_json(obj: Any) -> Any:
    """dict/list 안의 None 값을 제거해 OpenAI API처럼 null 키를 생략합니다.
    
    Args:
        obj (Any): dict/list 안의 None 값을 제거할 객체
    
    Returns:
        Any: dict/list 안의 None 값을 제거한 객체
    """
    
    if isinstance(obj, dict):
        return {k: _drop_none_json(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_drop_none_json(x) for x in obj]
    return obj


def _usage_detail_to_dict(v) -> Optional[dict]:
    """LiteLLM/OpenAI usage 세부 객체를 JSON 직렬화 가능한 dict로 변환합니다.
    
    Args:
        v: LiteLLM/OpenAI usage 세부 객체
    
    Returns:
        JSON 직렬화 가능한 dict 또는 None
    """
    
    if v is None:
        return None
    if hasattr(v, "model_dump"):
        return v.model_dump(exclude_none=True)
    if isinstance(v, dict):
        return v
    return None


@router.get("/models", summary="모델 목록 조회",
    description="사용 가능한 모델 목록을 OpenAI Compatible API 형식으로 조회합니다.",
    responses={
        200: {"description": "모델 목록을 반환합니다.", "model": ModelsListResponse},
        **DEFAULT_EXCEPTION_RESPONSES,
    }
)
async def get_models(
    db_manager: DatabaseManager = Depends(get_db_manager),
    logger: Logger = Depends(get_api_logger),
):
    # 활성화된 모델만 조회
    query = (db_models.Model.select()
                .where(db_models.Model.status == ModelStatus.RUNNING.value))
    models: list[db_items.Model] = await db_manager.select_items(query)
    
    model_list = [
        ModelInfo(
            id=model.name,
            created=int(model.created_at.timestamp()),
            object="model",
            owned_by=model.provider.value
        )
        for model in models
    ]
    
    return ModelsListResponse(
        object="list",
        data=model_list
    )

@router.get("/models/{model_name:path}", summary="모델 상세 조회",
    description="특정 모델의 상세 정보를 OpenAI Compatible API 형식으로 조회합니다.",
    responses={
        200: {"description": "모델 정보를 반환합니다.", "model": ModelInfo},
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 모델",
                            "value": {
                                "message": "등록되지 않은 모델이거나 실행중이 아닙니다.",
                                "target": "model_name={model_name}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES,
    }
)
async def get_model(
    model_name: str = Path(
        ...,
        description="조회할 모델명입니다.",
        examples=["gpt-4o-mini"]
    ),
    db_manager: DatabaseManager = Depends(get_db_manager),
    logger: Logger = Depends(get_api_logger),
):
    # 모델 조회
    query = (db_models.Model.select().where(
                (db_models.Model.name == model_name) &
                (db_models.Model.status == ModelStatus.RUNNING.value)))
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not model:
        return NotFoundResponse(
            message="등록되지 않은 모델이거나 실행중이 아닙니다.",
            target=f"model_name={model_name}"
        )

    return ModelInfo(
        id=model.name,
        created=int(model.created_at.timestamp()),
        object="model",
        owned_by=model.provider.value
    )

@router.post("/chat/completions", summary="Chat Completions",
    description=(
        "대화를 생성합니다.  \n"
        "OpenAI Compatible API 형식으로 요청 및 응답합니다.  \n"
        "스트리밍 모드를 지원합니다."
    ),
    responses={
        200: {"description": "대화 생성 결과를 반환합니다.", "model": ChatCompletionResponse},
        400: {
            "description": "잘못된 요청입니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_supported_model": {
                            "summary": "지원되지 않는 모델",
                            "value": {
                                "message": "해당 모델은 대화 생성에 사용할 수 없습니다.",
                                "target": "model={model}"
                            }
                        },
                        "empty_messages": {
                            "summary": "빈 messages",
                            "value": {
                                "message": "'messages'가 비어있습니다.",
                                "target": "messages={messages}"
                            }
                        },
                        "empty_content": {
                            "summary": "빈 content",
                            "value": {
                                "message": "'content'가 비어있습니다.",
                                "target": "messages[{idx}]={messages[idx]}"
                            }
                        },
                        "invalid_tool_call_id": {
                            "summary": "잘못된 tool_call_id",
                            "value": {
                                "message": "'role'이 'tool'인 경우 'tool_call_id'는 필수입니다.",
                                "target": "messages[{idx}]={messages[idx]}"
                            }
                        },
                        "missing_tool_response": {
                            "summary": "누락된 도구 응답 메시지",
                            "value": {
                                "message": "도구 호출 ID에 대한 응답 메시지가 누락되었습니다: {missing_tool_call_ids}",
                                "target": "messages[{idx}]={messages[idx]}"
                            }
                        },
                        "missing_json_in_messages": {
                            "summary": "messages에 json 문자열 누락",
                            "value": {
                                "message": "'json_object' 형식의 'response_format'을 사용하려면 'messages'에 어떤 형태로든 'json' 문자열이 포함되어 있어야 합니다.",
                                "target": "messages={messages}"
                            }
                        },
                        "missing_json_schema": {
                            "summary": "json_schema 누락",
                            "value": {
                                "message": "'json_schema' 형식의 'response_format'을 사용하려면 'json_schema' 필드가 필요합니다.",
                                "target": "response_format={response_format}"
                            }
                        },
                        "invalid_tool_choice_format": {
                            "summary": "잘못된 tool_choice 형식",
                            "value": {
                                "message": "'tool_choice' 형식이 올바르지 않습니다.",
                                "target": "tool_choice={tool_choice}"
                            }
                        },
                        "missing_parameter": {
                            "summary": "누락된 파라미터",
                            "value": {
                                "message": "파라미터가 누락되었습니다.",
                                "target": "{param}"
                            }
                        },
                        "file_download_error": {
                            "summary": "파일 다운로드 오류",
                            "value": {
                                "message": "파일을 다운로드하는 중에 오류가 발생했습니다.",
                                "target": "{file_url}"
                            }
                        },
                        "exceed_media_limit": {
                            "summary": "미디어 개수 초과",
                            "value": {
                                "message": "프롬프트마다 최대 {count}개의 {media_type}만 제공할 수 있습니다.",
                                "target": "messages={messages}"
                            }
                        },
                        "disabled_tool_choice_auto": {
                            "summary": "도구 자동 선택 기능 비활성화",
                            "value": {
                                "message": "모델 제공자 서버에서 도구 자동 선택(auto) 기능이 활성화되어 있지 않아 tool_choice='auto'를 사용할 수 없습니다.",
                                "target": "tool_choice={tool_choice}"
                            }
                        }
                    }
                }
            }
        },
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 모델",
                            "value": {
                                "message": "등록되지 않은 모델이거나 실행중이 아닙니다.",
                                "target": "model={model}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def chat_completions(
    payload: ChatCompletionsPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    start_time = time.time()
    
    # 모델 조회
    query = (db_models.Model.select().where(
                (db_models.Model.name == payload.model) &
                (db_models.Model.status == ModelStatus.RUNNING.value)))
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not model:
        return NotFoundResponse(
            message="등록되지 않은 모델이거나 실행중이 아닙니다.",
            target=f"model={payload.model}"
        )
    
    if model.model_type != ModelType.TEXT_GENERATION:
        return BadRequestResponse(
            message="해당 모델은 대화 생성에 사용할 수 없습니다.",
            target=f"model={payload.model}"
        )
    
    litellm_model = get_litellm_model_name(model.provider, model.name, model.model_id)
    litellm_params = get_litellm_params(model, user_info)
    
    # 메시지 변환
    messages = [msg.model_dump(mode="json", exclude_none=True) for msg in payload.messages]
    
    # 메시지 검증
    if not messages:
        return BadRequestResponse(
            message="'messages'가 비어있습니다.",
            target=f"messages={messages}",
        )
    
    tool_response_ids: set[str] = set()
    for msg in payload.messages or []:
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == ChatMessageRole.TOOL:
            tool_call_id = msg.get("tool_call_id") if isinstance(msg, dict) else getattr(msg, "tool_call_id", None)
            if tool_call_id:
                tool_response_ids.add(tool_call_id)
    
    for i, msg in enumerate(payload.messages or []):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        tool_calls = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
        tool_call_id = msg.get("tool_call_id") if isinstance(msg, dict) else getattr(msg, "tool_call_id", None)

        # content가 None인지 여부
        if content is None:
            # content가 list 이거나 role이 'assistant'이고 tool_calls 값이 있다면 허용
            if not (isinstance(content, list) or (role == ChatMessageRole.ASSISTANT and tool_calls)):
                return BadRequestResponse(
                    message="'content'가 비어있습니다.",
                    target=f"messages[{i}]={messages[i]}"
                )
        
        # tool_call_id 유효성 검사
        if role == ChatMessageRole.TOOL and not tool_call_id:
            return BadRequestResponse(
                message="'role'이 'tool'인 경우 'tool_call_id'는 필수입니다.",
                target=f"messages[{i}]={messages[i]}"
            )
        
        # tool_calls에 대한 응답 메시지 존재 여부
        if role == ChatMessageRole.ASSISTANT and tool_calls:
            missing_tool_call_ids = [
                tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                for tc in tool_calls
                if (tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)) not in tool_response_ids
            ]
            
            if missing_tool_call_ids:
                return BadRequestResponse(
                    message="도구 호출 ID에 대한 응답 메시지가 누락되었습니다: " + ", ".join(missing_tool_call_ids),
                    target=f"messages[{i}]={messages[i]}"
                )
    
    # 요청 데이터 검증 - Response Format (json_object) 사용 시 'json' 문자열이 포함되어 있는지 여부
    if payload.response_format and payload.response_format.type == "json_object":
        def _has_json(content):
            if isinstance(content, str):
                return "json" in content.lower()
            if isinstance(content, list):
                return any(
                    isinstance(p, dict)
                    and p.get("type") == "text"
                    and isinstance(p.get("text"), str)
                    and "json" in p["text"].lower()
                    for p in content
                )
            return False

        if not any(_has_json(m.get("content", "")) for m in messages):
            return BadRequestResponse(
                message="'json_object' 형식의 'response_format'을 사용하려면 'messages'에 어떤 형태로든 'json' 문자열이 포함되어 있어야 합니다.",
                target=f"messages={messages}",
            )
    
    # 요청 데이터 검증 - Response Format (json_schema) 사용 시 json_schema 필드 검증
    if (payload.response_format and
        payload.response_format.type == "json_schema" and
        not payload.response_format.json_schema):
            return BadRequestResponse(
                message="'json_schema' 형식의 'response_format'을 사용하려면 'json_schema' 필드가 필요합니다.",
                target=f"response_format={payload.response_format.model_dump(mode="json", exclude_none=True)}",
            )
    
    # LiteLLM 요청 파라미터 구성
    completion_params = {
        "model": litellm_model,
        "messages": messages,
        **litellm_params
    }
    
    # Optional 파라미터 추가
    if payload.temperature is not None:
        completion_params["temperature"] = payload.temperature
    if payload.top_p is not None:
        completion_params["top_p"] = payload.top_p
    if payload.n is not None:
        completion_params["n"] = payload.n
    if payload.stop is not None:
        completion_params["stop"] = payload.stop
    if payload.max_tokens is not None:
        completion_params["max_tokens"] = payload.max_tokens
    if payload.max_completion_tokens is not None:
        completion_params["max_completion_tokens"] = payload.max_completion_tokens
    if payload.presence_penalty is not None:
        completion_params["presence_penalty"] = payload.presence_penalty
    if payload.frequency_penalty is not None:
        completion_params["frequency_penalty"] = payload.frequency_penalty
    if payload.tools is not None:
        completion_params["tools"] = [tool.model_dump() for tool in payload.tools]
    if payload.tool_choice is not None:
        completion_params["tool_choice"] = payload.tool_choice
    if payload.response_format is not None:
        completion_params["response_format"] = payload.response_format.model_dump(exclude_none=True)
    if payload.seed is not None:
        completion_params["seed"] = payload.seed
    
    chat_template_kwargs = build_chat_template_kwargs(model.provider, payload.chat_template_kwargs)
    if chat_template_kwargs:
        completion_params["chat_template_kwargs"] = chat_template_kwargs
    
    request_id = f"chatcmpl-{util.generate_id(k=16)}"
    
    # 스트리밍 모드
    if payload.stream:
        completion_params["stream"] = True
        completion_params["stream_options"] = {"include_usage": True}
        
        async def generate_stream() -> AsyncGenerator[str, None]:
            input_tokens = 0
            cached_input_tokens = 0
            output_tokens = 0
            reasoning_tokens = 0

            try:
                response: CustomStreamWrapper = await litellm.acompletion(**completion_params)
                
                async for chunk in response:
                    # 스트림 청크 변환
                    choices = []
                    for choice in chunk.choices:
                        delta_content = None
                        delta_role = None
                        delta_tool_calls = None
                        
                        d = choice.delta if hasattr(choice, "delta") else None
                        if d is not None:
                            if hasattr(d, "content"):
                                delta_content = d.content
                            if hasattr(d, "role"):
                                delta_role = d.role
                            if hasattr(d, "tool_calls") and d.tool_calls:
                                delta_tool_calls = []
                                for tc in d.tool_calls:
                                    delta_tool_calls.append(tc.model_dump())
                        
                        delta_reasoning = getattr(d, "reasoning", None) if d else None
                        delta_reasoning_content = getattr(d, "reasoning_content", None) if d else None
                        delta_annotations = getattr(d, "annotations", None) if d else None
                        delta_audio = getattr(d, "audio", None) if d else None
                        delta_function_call = getattr(d, "function_call", None) if d else None
                        delta_refusal = getattr(d, "refusal", None) if d else None
                        
                        choices.append(ChatCompletionStreamChoice(
                            index=choice.index,
                            delta=ChatCompletionStreamDelta(
                                role=delta_role,
                                content=delta_content,
                                reasoning=delta_reasoning,
                                reasoning_content=delta_reasoning_content,
                                annotations=delta_annotations,
                                audio=delta_audio,
                                function_call=delta_function_call,
                                refusal=delta_refusal,
                                tool_calls=delta_tool_calls
                            ),
                            finish_reason=choice.finish_reason,
                            logprobs=getattr(choice, "logprobs", None),
                            stop_reason=getattr(choice, "stop_reason", None),
                            token_ids=getattr(choice, "token_ids", None),
                        ))
                    
                    # 마지막 청크에서 Usage 정보 추출
                    usage = None
                    if hasattr(chunk, "usage") and chunk.usage:
                        (
                            input_tokens,
                            cached_input_tokens,
                            output_tokens,
                            reasoning_tokens,
                        ) = extract_token_counts(chunk.usage)
                        usage = UsageInfo(
                            prompt_tokens=input_tokens,
                            completion_tokens=output_tokens,
                            total_tokens=input_tokens + output_tokens
                        )
                    
                    stream_response = ChatCompletionStreamResponse(
                        id=request_id,
                        created=int(time.time()),
                        object="chat.completion.chunk",
                        model=payload.model,
                        choices=choices,
                        usage=usage
                    )
                    
                    yield f"data: {stream_response.model_dump_json(exclude_none=True)}\n\n"
                
                yield "data: [DONE]\n\n"
                
                # 사용량 저장
                latency_ms = int((time.time() - start_time) * 1000)
                await save_usage(
                    user_info=user_info,
                    model_name=payload.model,
                    model_type=ModelType.TEXT_GENERATION,
                    usage=TextGenerationUsageModel(
                        input_tokens=input_tokens,
                        cached_input_tokens=cached_input_tokens,
                        output_tokens=output_tokens,
                        reasoning_tokens=reasoning_tokens,
                    ),
                    request_id=request_id,
                    latency_ms=latency_ms,
                    metadata={"stream": True},
                    db_manager=db_manager
                )
            except Exception as exc:
                error_message = str(exc)
                logger.exception("Chat Completions 스트리밍 요청 처리 중 오류가 발생했습니다.")
                
                # 사용량 저장 (오류)
                latency_ms = int((time.time() - start_time) * 1000)
                await save_usage(
                    user_info=user_info,
                    model_name=payload.model,
                    model_type=ModelType.TEXT_GENERATION,
                    request_id=request_id,
                    latency_ms=latency_ms,
                    status=ModelUsageStatus.ERROR,
                    error_message=error_message[:1024],
                    metadata={"stream": True},
                    db_manager=db_manager
                )
                
                yield f"data: {{\"error\": \"{error_message}\"}}\n\n"
        
        try:
            return StreamingResponse(
                generate_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )
        except litellm.exceptions.APIConnectionError as exc:
            if "Invalid tool choice" in str(exc):
                return BadRequestResponse(
                    message="'tool_choice' 형식이 올바르지 않습니다.",
                    target=f"tool_choice={payload.tool_choice}"
                )
            raise exc
        except litellm.exceptions.BadRequestError as exc:
            if "Missing required parameter" in str(exc):
                return BadRequestResponse(
                    message="파라미터가 누락되었습니다.",
                    target=exc.param
                )
            elif "Error while downloading" in str(exc):
                return BadRequestResponse(
                    message="파일을 다운로드하는 중에 오류가 발생했습니다.",
                    target=exc.message.split("Error while downloading")[-1].strip().rstrip("."),
                )
            elif "At most" in str(exc) and "may be provided in one prompt." in str(exc):
                raw_segment = str(exc).split("At most")[-1].split("may be provided in one prompt.")[0].strip()
                parts = raw_segment.split()

                if len(parts) >= 2:
                    count, unit = parts[0], parts[1]
                    media_type = "이미지" if "image" in unit else "비디오"
                else:
                    count, media_type = "0", "미디어"

                return BadRequestResponse(
                    message=f"프롬프트마다 최대 {count}개의 {media_type}만 제공할 수 있습니다.",
                    target=f"messages={messages}",
                )
            elif '"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set' in str(exc):
                return BadRequestResponse(
                    message="모델 제공자 서버에서 도구 자동 선택(auto) 기능이 활성화되어 있지 않아 tool_choice='auto'를 사용할 수 없습니다.",
                    target=f"tool_choice={payload.tool_choice}"
                )
            raise exc
        except litellm.exceptions.InternalServerError as exc:
            if "Not Found" in exc.message and "url='" in exc.message:
                return BadRequestResponse(
                    message="파일을 다운로드하는 중에 오류가 발생했습니다.",
                    target=exc.message.split("url='")[-1].rstrip("'") if "url='" in exc.message else "unknown",
                )
            raise exc
    
    # 비스트리밍 모드
    try:
        response: ModelResponse = await litellm.acompletion(**completion_params)
        
        # 응답 변환
        choices = []
        for choice in response.choices:
            # tool_calls가 있으면 딕셔너리로 변환
            tool_calls_raw = getattr(choice.message, "tool_calls", None)
            tool_calls = None
            if tool_calls_raw:
                tool_calls = [
                    tc.model_dump() if hasattr(tc, "model_dump") else dict(tc)
                    for tc in tool_calls_raw
                ]
            
            msg = choice.message
            message = ChatCompletionMessage(
                role=msg.role,
                content=msg.content,
                reasoning=getattr(msg, "reasoning", None),
                reasoning_content=getattr(msg, "reasoning_content", None),
                annotations=getattr(msg, "annotations", None),
                audio=getattr(msg, "audio", None),
                function_call=getattr(msg, "function_call", None),
                tool_calls=tool_calls,
                refusal=getattr(msg, "refusal", None),
            )
            choices.append(ChatCompletionChoice(
                index=choice.index,
                message=message,
                finish_reason=choice.finish_reason,
                logprobs=getattr(choice, "logprobs", None),
                stop_reason=getattr(choice, "stop_reason", None),
                token_ids=getattr(choice, "token_ids", None),
            ))
        
        # Usage 정보
        # 응답 본문은 상위 제공자가 준 값을 그대로 반영한다. (completion_tokens_details에 추론
        # 토큰이 들어 있어 클라이언트가 확인할 수 있다) 내부 과금 집계만 extract_token_counts로
        # 보정해 추론 토큰이 출력 토큰에서 누락되지 않게 한다.
        usage = None
        if response.usage:
            prompt_tokens_details = getattr(response.usage, "prompt_tokens_details", None)
            completion_tokens_details = getattr(response.usage, "completion_tokens_details", None)
            usage = UsageInfo(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
                prompt_tokens_details=_usage_detail_to_dict(prompt_tokens_details),
                completion_tokens_details=_usage_detail_to_dict(completion_tokens_details),
            )

        input_tokens, cached_input_tokens, output_tokens, reasoning_tokens = extract_token_counts(
            response.usage
        )

        # 사용량 저장
        latency_ms = int((time.time() - start_time) * 1000)
        await save_usage(
            user_info=user_info,
            model_name=payload.model,
            model_type=ModelType.TEXT_GENERATION,
            usage=TextGenerationUsageModel(
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
            ),
            request_id=request_id,
            latency_ms=latency_ms,
            metadata={"stream": False},
            db_manager=db_manager
        )
        
        dumped = ChatCompletionResponse(
            id=request_id,
            created=int(time.time()),
            object="chat.completion",
            model=payload.model,
            choices=choices,
            usage=usage,
            service_tier=getattr(response, "service_tier", None),
            system_fingerprint=getattr(response, "system_fingerprint", None),
            prompt_logprobs=getattr(response, "prompt_logprobs", None),
            prompt_token_ids=getattr(response, "prompt_token_ids", None),
            kv_transfer_params=getattr(response, "kv_transfer_params", None),
        ).model_dump(mode="json", exclude_none=True)
        return _drop_none_json(dumped)
    except litellm.exceptions.APIConnectionError as exc:
        if "Invalid tool choice" in str(exc):
            return BadRequestResponse(
                message="'tool_choice' 형식이 올바르지 않습니다.",
                target=f"tool_choice={payload.tool_choice}"
            )
        raise exc
    except litellm.exceptions.BadRequestError as exc:
        if "Missing required parameter" in str(exc):
            return BadRequestResponse(
                message="파라미터가 누락되었습니다.",
                target=exc.param
            )
        elif "Error while downloading" in str(exc):
            return BadRequestResponse(
                message="파일을 다운로드하는 중에 오류가 발생했습니다.",
                target=exc.message.split("Error while downloading")[-1].strip().rstrip("."),
            )
        elif "At most" in str(exc) and "may be provided in one prompt." in str(exc):
            raw_segment = str(exc).split("At most")[-1].split("may be provided in one prompt.")[0].strip()
            parts = raw_segment.split()

            if len(parts) >= 2:
                count, unit = parts[0], parts[1]
                media_type = "이미지" if "image" in unit else "비디오"
            else:
                count, media_type = "0", "미디어"

            return BadRequestResponse(
                message=f"프롬프트마다 최대 {count}개의 {media_type}만 제공할 수 있습니다.",
                target=f"messages={messages}",
            )
        elif '"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set' in str(exc):
            return BadRequestResponse(
                message="모델 제공자 서버에서 도구 자동 선택(auto) 기능이 활성화되어 있지 않아 tool_choice='auto'를 사용할 수 없습니다.",
                target=f"tool_choice={payload.tool_choice}"
            )
        raise exc
    except litellm.exceptions.InternalServerError as exc:
        if "Not Found" in exc.message and "url='" in exc.message:
            return BadRequestResponse(
                message="파일을 다운로드하는 중에 오류가 발생했습니다.",
                target=exc.message.split("url='")[-1].rstrip("'") if "url='" in exc.message else "unknown",
            )
        raise exc
    except Exception as exc:
        error_message = str(exc)
        logger.exception("Chat Completions 요청 처리 중 오류가 발생했습니다.")
        
        # 사용량 저장 (오류)
        latency_ms = int((time.time() - start_time) * 1000)
        await save_usage(
            user_info=user_info,
            model_name=payload.model,
            model_type=ModelType.TEXT_GENERATION,
            request_id=request_id,
            latency_ms=latency_ms,
            status=ModelUsageStatus.ERROR,
            error_message=error_message[:1024],
            metadata={"stream": False},
            db_manager=db_manager
        )
        
        return InternalServerErrorResponse(
            message="서버 내부 오류가 발생했습니다."
        )

@router.post("/embeddings", summary="Embeddings",
    description=(
        "텍스트 임베딩을 생성합니다.  \n"
        "OpenAI Compatible API 형식으로 요청 및 응답합니다."
    ),
    responses={
        200: {"description": "임베딩 결과를 반환합니다.", "model": EmbeddingsResponse},
        400: {
            "description": "잘못된 요청입니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_supported_model": {
                            "summary": "지원되지 않는 모델",
                            "value": {
                                "message": "해당 모델은 임베딩에 사용할 수 없습니다.",
                                "target": "model={model}"
                            }
                        }
                    }
                }
            }
        },
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 모델",
                            "value": {
                                "message": "등록되지 않은 모델이거나 실행중이 아닙니다.",
                                "target": "model={model}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def create_embeddings(
    payload: EmbeddingsPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    start_time = time.time()
    
    # 모델 조회
    query = (db_models.Model.select().where(
                (db_models.Model.name == payload.model) &
                (db_models.Model.status == ModelStatus.RUNNING.value)))
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not model:
        return NotFoundResponse(
            message="등록되지 않은 모델이거나 실행중이 아닙니다.",
            target=f"model={payload.model}"
        )
    
    if model.model_type != ModelType.EMBEDDING:
        return BadRequestResponse(
            message="해당 모델은 임베딩에 사용할 수 없습니다.",
            target=f"model={payload.model}"
        )
    
    request_id = f"embd-{util.generate_id(k=16)}"

    extra_params = {}
    if payload.encoding_format is not None:
        extra_params["encoding_format"] = payload.encoding_format

    try:
        response: EmbeddingResponse = await create_embedding(
            model, user_info, payload.input, **extra_params
        )
        
        # 응답 변환
        data = []
        for item in response.data:
            data.append(EmbeddingData(
                object="embedding",
                index=item["index"],
                embedding=item["embedding"]
            ))
        
        # Usage 정보
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        total_tokens = response.usage.total_tokens if response.usage else 0
        
        usage = EmbeddingUsage(
            prompt_tokens=input_tokens,
            total_tokens=total_tokens
        )
        
        # 사용량 저장
        is_batch = isinstance(payload.input, list) and len(payload.input) > 1
        latency_ms = int((time.time() - start_time) * 1000)
        await save_usage(
            user_info=user_info,
            model_name=payload.model,
            model_type=ModelType.EMBEDDING,
            usage=EmbeddingUsageModel(
                single_tokens=0 if is_batch else input_tokens,
                batch_tokens=input_tokens if is_batch else 0,
            ),
            request_id=request_id,
            latency_ms=latency_ms,
            db_manager=db_manager
        )
        
        dumped = EmbeddingsResponse(
            id=request_id,
            created=int(time.time()),
            object="list",
            data=data,
            model=payload.model,
            usage=usage
        ).model_dump(mode="json", exclude_none=True)
        return _drop_none_json(dumped)
    except Exception as exc:
        error_message = str(exc)
        logger.exception("Embeddings 요청 처리 중 오류가 발생했습니다.")
        
        # 사용량 저장 (오류)
        latency_ms = int((time.time() - start_time) * 1000)
        await save_usage(
            user_info=user_info,
            model_name=payload.model,
            model_type=ModelType.EMBEDDING,
            request_id=request_id,
            latency_ms=latency_ms,
            status=ModelUsageStatus.ERROR,
            error_message=error_message[:1024],
            db_manager=db_manager
        )
        
        return InternalServerErrorResponse(
            message="서버 내부 오류가 발생했습니다."
        )

# TODO: 추후 Rerank 테스트 필요
@router.post("/rerank", summary="(개발 중) Rerank Documents",
    description=(
        "문서 리랭크를 수행합니다.  \n"
        "쿼리와 문서 목록을 받아 관련성 순으로 정렬합니다."
    ),
    responses={
        200: {"description": "리랭크 결과를 반환합니다.", "model": RerankResponse},
        400: {
            "description": "잘못된 요청입니다.",
            "model": BadRequestError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_supported_model": {
                            "summary": "지원되지 않는 모델",
                            "value": {
                                "message": "해당 모델은 문서 리랭크에 사용할 수 없습니다.",
                                "target": "model={model}"
                            }
                        }
                    }
                }
            }
        },
        404: {
            "description": "등록되지 않은 항목입니다.", 
            "model": NotFoundError,
            "content": {
                "application/json": {
                    "examples": {
                        "not_found": {
                            "summary": "등록되지 않은 모델",
                            "value": {
                                "message": "등록되지 않은 모델이거나 실행중이 아닙니다.",
                                "target": "model={model}"
                            }
                        }
                    }
                }
            }
        },
        **DEFAULT_EXCEPTION_RESPONSES_WITH_UNAUTHORIZED,
    }
)
async def rerank_documents(
    payload: RerankPayload,
    db_manager: DatabaseManager = Depends(get_db_manager),
    user_info: TokenUserInfo = Depends(auth.get_user_info),
    logger: Logger = Depends(get_api_logger),
):
    start_time = time.time()
    
    # 모델 조회
    query = (db_models.Model.select().where(
                (db_models.Model.name == payload.model) &
                (db_models.Model.status == ModelStatus.RUNNING.value)))
    model: Optional[db_items.Model] = await db_manager.select_item(query)
    if not model:
        return NotFoundResponse(
            message="등록되지 않은 모델이거나 실행중이 아닙니다.",
            target=f"model={payload.model}"
        )
    
    if model.model_type != ModelType.RERANK:
        return BadRequestResponse(
            message="해당 모델은 문서 리랭크에 사용할 수 없습니다.",
            target=f"model={payload.model}"
        )
    
    litellm_model = get_litellm_model_name(model.provider, model.name, model.model_id)
    litellm_params = get_litellm_params(model, user_info)
    
    request_id = f"rerank-{util.generate_id(k=16)}"
    
    # 문서 텍스트 추출
    documents = []
    for doc in payload.documents:
        if isinstance(doc, str):
            documents.append(doc)
        else:
            documents.append(doc.text)
    
    try:
        response = await litellm.arerank(
            model=litellm_model,
            query=payload.query,
            documents=documents,
            top_n=payload.top_n,
            **litellm_params
        )
        
        # 응답 변환
        results = []
        for item in response.results:
            document = None
            if payload.return_documents:
                original_doc = payload.documents[item.index]
                if isinstance(original_doc, str):
                    document = original_doc
                else:
                    document = original_doc.model_dump()
            
            results.append(RerankResult(
                index=item.index,
                relevance_score=item.relevance_score,
                document=document
            ))
        
        # 사용량 저장
        latency_ms = int((time.time() - start_time) * 1000)
        await save_usage(
            user_info=user_info,
            model_name=payload.model,
            model_type=ModelType.RERANK,
            usage=RerankUsageModel(),
            request_id=request_id,
            latency_ms=latency_ms,
            metadata={"query": payload.query, "documents_count": len(documents)},
            db_manager=db_manager
        )
        
        dumped = RerankResponse(
            id=request_id,
            model=payload.model,
            results=results,
            usage=getattr(response, 'usage', None)
        ).model_dump(mode="json", exclude_none=True)
        return _drop_none_json(dumped)
        
    except Exception as exc:
        error_message = str(exc)
        logger.error(f"리랭크 오류: {error_message}")
        
        # 사용량 저장 (오류)
        latency_ms = int((time.time() - start_time) * 1000)
        await save_usage(
            user_info=user_info,
            model_name=payload.model,
            model_type=ModelType.RERANK,
            request_id=request_id,
            latency_ms=latency_ms,
            status=ModelUsageStatus.ERROR,
            error_message=error_message[:1024],
            db_manager=db_manager
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_message
        )
