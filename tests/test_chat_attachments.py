"""챗봇 첨부 헬퍼 단위 테스트입니다.

외부 의존성(S3·Doc Parser·LLM) 없이 순수 변환·검증만 확인합니다.
"""

from app.utils.chat_attachments import (
    MAX_ATTACHMENT_BYTES,
    ResolvedAttachment,
    attachment_object_prefix,
    attachments_to_storage,
    build_object_key,
    build_user_content,
    default_query_for_attachments,
    extract_plain_text,
    guess_kind,
    image_data_uri,
    is_allowed_upload,
    join_parsed_pages,
    owns_object_key,
    resolve_mime,
    safe_file_name,
    truncate_text,
)


def test_guess_kind_by_extension_and_mime():
    assert guess_kind("a.png") == "image"
    assert guess_kind("note.PDF") == "document"
    assert guess_kind("x.bin") is None
    assert guess_kind("x.bin", "image/jpeg") == "image"
    assert guess_kind("x.bin", "application/pdf") == "document"


def test_is_allowed_upload_limits():
    ok, _ = is_allowed_upload("a.png", "image/png", 100)
    assert ok

    ok, msg = is_allowed_upload("a.png", "image/png", 0)
    assert not ok
    assert "비어" in msg

    ok, msg = is_allowed_upload("a.png", "image/png", MAX_ATTACHMENT_BYTES + 1)
    assert not ok
    assert "10MB" in msg

    ok, msg = is_allowed_upload("a.exe", "application/octet-stream", 100)
    assert not ok
    assert "형식" in msg


def test_object_key_ownership():
    key = build_object_key("20241234", "id-1", "시간표.png")
    assert key.startswith(attachment_object_prefix("20241234"))
    assert owns_object_key("20241234", key)
    assert not owns_object_key("other-user", key)
    assert not owns_object_key("20241234", "../etc/passwd")
    assert not owns_object_key("20241234", "/absolute")


def test_safe_file_name_strips_path():
    assert "/" not in safe_file_name("../../secret.pdf")
    assert safe_file_name("a/b/c.docx").endswith("c.docx")


def test_join_parsed_pages_and_truncate():
    text = join_parsed_pages({
        "contents": [
            {"content": "첫 페이지", "page": 1},
            {"content": " 둘째 페이지 ", "page": 2},
            {"content": "", "page": 3},
        ]
    })
    assert text == "첫 페이지\n\n둘째 페이지"
    assert join_parsed_pages(None) == ""
    assert join_parsed_pages({}) == ""

    assert truncate_text("abcdef", 3) == "abc"
    assert "이하 생략" in truncate_text("abcdefghijklmnop", 12)


def test_image_data_uri_and_user_content():
    uri = image_data_uri("image/png", b"\x89PNG")
    assert uri.startswith("data:image/png;base64,")

    docs = [
        ResolvedAttachment(
            attachment_id="1",
            file_name="a.pdf",
            file_type="application/pdf",
            kind="document",
            object_key="k",
            text="문서 본문",
        )
    ]
    content = build_user_content("요약해줘", docs)
    assert isinstance(content, str)
    assert "[첨부 문서: a.pdf]" in content
    assert "문서 본문" in content
    assert "요약해줘" in content

    images = [
        ResolvedAttachment(
            attachment_id="2",
            file_name="a.png",
            file_type="image/png",
            kind="image",
            object_key="k2",
            data_uri=uri,
        )
    ]
    multi = build_user_content("이게 뭐야?", images)
    assert isinstance(multi, list)
    # Gemma: 이미지를 텍스트보다 앞에 둔다.
    assert multi[0]["type"] == "image_url"
    assert multi[0]["image_url"]["url"] == uri
    assert multi[-1]["type"] == "text"
    assert "이게 뭐야?" in multi[-1]["text"]

    only_image = build_user_content("", images)
    assert isinstance(only_image, list)
    assert only_image[0]["type"] == "image_url"
    assert "첨부한" in only_image[-1]["text"] or "설명" in only_image[-1]["text"]

    pdf_pages = [
        ResolvedAttachment(
            attachment_id="3",
            file_name="resume.pdf",
            file_type="application/pdf",
            kind="document",
            object_key="k3",
            data_uris=[uri, uri],
        )
    ]
    pdf_content = build_user_content("피드백 해줘", pdf_pages)
    assert isinstance(pdf_content, list)
    assert sum(1 for part in pdf_content if part["type"] == "image_url") == 2
    assert pdf_content[-1]["type"] == "text"
    assert "피드백" in pdf_content[-1]["text"]


def test_default_query_and_storage():
    assert default_query_for_attachments("") 
    assert "첨부" in default_query_for_attachments("   ")
    assert default_query_for_attachments("질문") == "질문"

    stored = attachments_to_storage([{
        "attachment_id": "1",
        "file_name": "a.png",
        "file_type": "image/png",
        "kind": "image",
        "object_key": "chat-attachments/u/a.png",
        "size_bytes": 10,
    }])
    assert stored[0]["kind"] == "image"
    assert attachments_to_storage(None) is None


def test_resolve_mime_fallback():
    assert resolve_mime("a.png", "image/png") == "image/png"
    assert resolve_mime("a.webp", "application/octet-stream") == "image/webp"
    assert resolve_mime("doc.pdf", None) == "application/pdf"


def test_extract_plain_text_for_txt_md_only():
    assert extract_plain_text("안녕하세요".encode("utf-8"), "note.txt") == "안녕하세요"
    assert extract_plain_text("# 제목\n본문".encode("utf-8"), "a.md") == "# 제목\n본문"
    assert extract_plain_text("hello".encode("utf-8"), "a.pdf") is None
    assert extract_plain_text(b"", "a.txt") is None
