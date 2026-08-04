"""챗봇 첨부 파일 검증·해석·프롬프트 조립 헬퍼입니다.

업로드 API와 `send_chat_message` / `chat_graph.generate`가 공유합니다.
S3·파서 I/O는 호출 쪽에 두고, 이 모듈은 순수 변환과 검증만 담당합니다.
"""

from __future__ import annotations

import base64
import re
import uuid

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Literal, Optional, Union

AttachmentKind = Literal["image", "document"]

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
"""파일 하나당 최대 크기 (10MB)"""

MAX_ATTACHMENTS_PER_MESSAGE = 5
"""메시지당 최대 첨부 개수"""

IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp", "gif"})
DOCUMENT_EXTENSIONS = frozenset({"pdf", "doc", "docx", "txt", "hwp", "hwpx", "md"})

IMAGE_MIMES = frozenset({
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
})
DOCUMENT_MIMES = frozenset({
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
    "application/x-hwp",
    "application/haansofthwp",
    "application/vnd.hancom.hwp",
    "application/vnd.hancom.hwpx",
    "application/hwpx",
})

EXT_TO_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "md": "text/markdown",
    "hwp": "application/x-hwp",
    "hwpx": "application/vnd.hancom.hwpx",
}

OBJECT_KEY_ROOT = "chat-attachments"
"""S3 객체 키 루트 접두사"""

_UNSAFE_NAME_RE = re.compile(r"[^\w.\-()+\u3130-\u318F\uAC00-\uD7A3]+", re.UNICODE)


@dataclass(frozen=True)
class ResolvedAttachment:
    """모델 입력용으로 해석된 첨부입니다.

    이미지면 `data_uri`, 문서면 `text`가 채워집니다. DB에는 넣지 않습니다.
    """

    attachment_id: str
    file_name: str
    file_type: str
    kind: AttachmentKind
    object_key: str
    size_bytes: Optional[int] = None
    data_uri: Optional[str] = None
    text: Optional[str] = None


def file_extension(file_name: str) -> str:
    """파일명에서 소문자 확장자를 추출합니다."""

    suffix = PurePosixPath(file_name or "").suffix
    return suffix.lstrip(".").lower()


def guess_kind(file_name: str, file_type: Optional[str] = None) -> Optional[AttachmentKind]:
    """확장자·MIME으로 image/document 여부를 판별합니다. 허용 밖이면 None."""

    mime = (file_type or "").strip().lower()
    ext = file_extension(file_name)

    if mime in IMAGE_MIMES or ext in IMAGE_EXTENSIONS:
        return "image"
    if mime in DOCUMENT_MIMES or ext in DOCUMENT_EXTENSIONS:
        return "document"
    return None


def resolve_mime(file_name: str, file_type: Optional[str] = None) -> str:
    """업로드에 쓸 Content-Type을 결정합니다."""

    mime = (file_type or "").strip().lower()
    if mime and mime != "application/octet-stream":
        return mime
    ext = file_extension(file_name)
    return EXT_TO_MIME.get(ext, "application/octet-stream")


def is_allowed_upload(file_name: str, file_type: Optional[str], size_bytes: int) -> tuple[bool, str]:
    """업로드 허용 여부를 (ok, error_message)로 반환합니다."""

    if size_bytes <= 0:
        return False, "파일이 비어있습니다."
    if size_bytes > MAX_ATTACHMENT_BYTES:
        return False, f"파일 크기는 {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB 이하여야 합니다."
    if guess_kind(file_name, file_type) is None:
        return False, "지원하지 않는 파일 형식입니다."
    return True, ""


def safe_file_name(file_name: str) -> str:
    """S3 키·표시용으로 파일명을 정리합니다."""

    name = PurePosixPath(file_name or "file").name.strip() or "file"
    name = _UNSAFE_NAME_RE.sub("_", name)
    return name[:180] or "file"


def attachment_object_prefix(user_id: str) -> str:
    """사용자 소유 첨부 객체의 키 접두사입니다."""

    safe_user = safe_file_name(user_id or "anonymous")
    return f"{OBJECT_KEY_ROOT}/{safe_user}/"


def build_object_key(user_id: str, attachment_id: str, file_name: str, now: Optional[datetime] = None) -> str:
    """첨부를 저장할 S3 object key를 만듭니다."""

    stamp = now or datetime.utcnow()
    return (
        f"{attachment_object_prefix(user_id)}"
        f"{stamp.year:04d}/{stamp.month:02d}/"
        f"{attachment_id}_{safe_file_name(file_name)}"
    )


def owns_object_key(user_id: str, object_key: str) -> bool:
    """object_key가 해당 사용자 업로드 경로인지 확인합니다."""

    if not object_key or ".." in object_key or object_key.startswith("/"):
        return False
    return object_key.startswith(attachment_object_prefix(user_id))


def new_attachment_id() -> str:
    """첨부 ID를 생성합니다."""

    return str(uuid.uuid4())


def image_data_uri(mime: str, data: bytes) -> str:
    """이미지 바이트를 data URI로 인코딩합니다."""

    content_type = mime or "application/octet-stream"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def join_parsed_pages(parsed: Optional[dict[str, Any]]) -> str:
    """doc_parser 결과의 페이지 텍스트를 이어 붙입니다."""

    if not parsed:
        return ""
    parts: list[str] = []
    for item in parsed.get("contents") or []:
        content = (item or {}).get("content")
        if content:
            parts.append(str(content).strip())
    return "\n\n".join(part for part in parts if part).strip()


def truncate_text(text: Optional[str], limit: int, marker: str = " …(이하 생략)") -> str:
    """길이 예산으로 텍스트를 자릅니다."""

    value = text or ""
    if limit <= 0 or len(value) <= limit:
        return value
    if len(marker) >= limit:
        return value[:limit]
    return value[: limit - len(marker)] + marker


def attachments_to_storage(attachments: Optional[list[Any]]) -> Optional[list[dict]]:
    """DB JSON 컬럼에 넣을 메타 목록으로 직렬화합니다."""

    if not attachments:
        return None
    result: list[dict] = []
    for item in attachments:
        if hasattr(item, "model_dump"):
            result.append(item.model_dump(mode="json"))
        elif isinstance(item, dict):
            result.append({
                "attachment_id": item.get("attachment_id"),
                "file_name": item.get("file_name"),
                "file_type": item.get("file_type"),
                "kind": item.get("kind"),
                "object_key": item.get("object_key"),
                "size_bytes": item.get("size_bytes"),
            })
    return result


def build_user_content(
    query: str,
    resolved: Optional[list[ResolvedAttachment]] = None,
    query_max_chars: int = 8000,
) -> Union[str, list[dict]]:
    """최종 LLM user 메시지 content를 조립합니다.

    이미지가 있으면 OpenAI 호환 multimodal list, 아니면 문자열을 반환합니다.
    """

    query_text = truncate_text((query or "").strip(), query_max_chars)
    resolved = resolved or []

    document_blocks: list[str] = []
    image_parts: list[dict] = []

    for item in resolved:
        if item.kind == "document" and item.text:
            document_blocks.append(f"[첨부 문서: {item.file_name}]\n{item.text}")
        elif item.kind == "image" and item.data_uri:
            image_parts.append({
                "type": "image_url",
                "image_url": {"url": item.data_uri},
            })

    text_sections: list[str] = []
    if document_blocks:
        text_sections.append("\n\n".join(document_blocks))
    if query_text:
        text_sections.append(query_text)
    elif not document_blocks and image_parts:
        text_sections.append("첨부한 파일 내용을 바탕으로 설명해 주세요.")
    elif not document_blocks and not image_parts:
        text_sections.append(query_text)

    text_body = "\n\n".join(section for section in text_sections if section).strip()

    if not image_parts:
        return text_body

    parts: list[dict] = [{"type": "text", "text": text_body or "첨부한 이미지를 확인해 주세요."}]
    parts.extend(image_parts)
    return parts


def default_query_for_attachments(message: str) -> str:
    """첨부만 있고 질문이 비어 있을 때 그래프에 넣을 기본 질문입니다."""

    text = (message or "").strip()
    if text:
        return text
    return "첨부한 파일 내용을 바탕으로 설명해 주세요."
