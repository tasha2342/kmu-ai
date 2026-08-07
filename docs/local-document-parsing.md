# 로컬 문서 파싱 (Doc Parser 대체)

외부 Doc Parser HTTP API를 제거하고, 컨테이너 안에서 포맷별로 로컬 파싱합니다.

관련 코드:

- [`app/utils/local_doc_parse.py`](../app/utils/local_doc_parse.py) — 파사드
- [`app/utils/doc_parser.py`](../app/utils/doc_parser.py) — 호환 래퍼 (`parse_document` → 로컬)
- 챗 첨부: [`app/api/v1/endpoints/chat.py`](../app/api/v1/endpoints/chat.py) `_resolve_attachments`
- RAG: [`app/api/v1/endpoints/document.py`](../app/api/v1/endpoints/document.py) upload/parse

학칙 인제스트([`hwp_extractor.py`](../app/utils/hwp_extractor.py) / pyhwp)는 **그대로**입니다.

## 포맷별 경로

| 확장자 | 추출 | 챗봇 멀티모달 이미지 |
| --- | --- | --- |
| `hwp`, `hwpx` | [rhwp](https://github.com/edwardkim/rhwp) `export-pdf` → pypdfium2 텍스트 | 상위 N페이지 PNG |
| `pdf` | pypdfium2 텍스트 | 상위 N페이지 PNG |
| `docx` | python-docx (문단 + 표 Markdown) | 표 PNG (Pillow) |
| `txt`, `md` | UTF-8 등 디코딩 | 없음 |
| `doc` | **미지원** | — |

- RAG(`/document/upload`, `/document/parse`): **텍스트만** 색인 (`for_chat=False`)
- 챗봇 첨부: 텍스트 + 페이지/표 이미지를 Gemma 멀티모달로 함께 전달 (`for_chat=True`)

```text
HWP ── rhwp export-pdf ──┐
PDF ─────────────────────┼─ pypdfium2 ─ texts (+ page PNGs if chat)
DOCX ─ python-docx ──────┘
```

## 설정

`configs/config.yaml` / template:

```yaml
chatbot:
  attachment_image_max_pages: 4   # 챗 멀티모달 페이지/표 상한
  rhwp_timeout_seconds: 120       # HWP→PDF 타임아웃

# deprecated — 무시됨 (호환용)
doc_parser:
  api_url: ""
  api_key: null
```

환경 변수:

- `RHWP_BIN` — rhwp 실행 파일 절대 경로 (Docker 기본: `/usr/local/bin/rhwp`)

## Docker

`Dockerfile` 런타임 스테이지에 rhwp **v0.8.2** linux-x86_64 바이너리를 설치합니다.
Noto CJK(`fonts-noto-cjk`)를 폰트 경로·fallback으로 넘깁니다.

재빌드:

```bash
docker compose up -d --build kmu-ai-api
```

## 한계

- legacy `.doc`는 지원하지 않습니다. DOCX/PDF/HWP로 변환해 주세요.
- rhwp PDF에 폰트가 없으면 한글이 빠질 수 있습니다. 이미지엔 Noto를 반드시 포함하세요.
- 페이지 이미지 수는 `attachment_image_max_pages`로 제한됩니다 (토큰·지연).
- HOP GUI는 사용하지 않습니다. 엔진만 [rhwp](https://github.com/edwardkim/rhwp) CLI입니다.

## 검증 체크리스트

1. `rhwp --help`가 API 컨테이너에서 동작
2. 샘플 HWP 첨부 → 답변이 본문/표 내용을 반영
3. DOCX 표 첨부 → 표 이미지 + Markdown 텍스트가 프롬프트에 포함
4. RAG `/document/upload`가 Doc Parser URL 없이도 청크 생성
5. `python -m pytest tests/test_local_doc_parse.py -q`

통합 smoke (호스트에 `RHWP_BIN` 설정 시):

```bash
export RHWP_BIN=/usr/local/bin/rhwp
pytest tests/test_local_doc_parse.py -q -k rhwp_smoke
```
