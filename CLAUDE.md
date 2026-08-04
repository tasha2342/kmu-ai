# kmu-ai

계명대학교 AI 챗봇. FastAPI 백엔드 + React/Vite 프론트엔드.
기능 요구사항은 계명대 기능정의서의 `KAI-REQ-###` 번호로 코드 주석에 표기되어 있습니다.

> Gemma 4의 thinking·토큰 집계 등 모델 관련 주의사항은 상위 `../CLAUDE.md`를 먼저 보세요.

## 구조

```
app/
├── api/v1/endpoints/   # chat, document, faq, rag, graphrag, model, model_use, chatbot_admin, ...
├── utils/
│   ├── chat_graph.py       # 챗봇 오케스트레이션 (LangGraph). 의도 분류 -> 검색 -> 응답 -> 요약
│   ├── litellm.py          # 모든 LLM 호출의 단일 통로
│   ├── local_doc_parse.py  # Doc Parser 대체. HWP(rhwp)+PDF/DOCX/TXT 로컬 파싱
│   ├── embedder.py         # 임베딩을 컨테이너 안에서 직접 실행 (GPU 노드로 안 나감)
│   ├── node_client.py      # pet-pass-one-node 호출 (GPU 모델 실행/중지)
│   └── vector_store.py, graphrag.py, memgraph.py
├── models/                 # enum, config, database(peewee), db_item, cost
frontend/src/
├── pages/chat/             # 챗봇 대화
├── pages/admin/            # 관리자 (FAQ, RAG, 통계, 로그, 마스킹, 미응답)
└── lib/                    # api.js, chatStream.js, authStore.js, endpoints.js
```

## 모델은 코드가 아니라 DB에 등록된다

`models` 테이블이 진실의 원천입니다. 코드에 모델 ID를 박아두지 마세요.

- `provider`(`ModelProvider`)로 호출 경로가 갈립니다. `LOCAL`만 GPU 노드를 쓰고,
  나머지는 LiteLLM이 외부 제공자로 보냅니다.
- `get_litellm_model_name()`이 `{provider}/{model_id}` 형태를 만듭니다.
  예: `provider=gemini, model_id=gemma-4-31b-it` → `gemini/gemma-4-31b-it`
- **원격 모델은 등록 즉시 `RUNNING`이 됩니다.** (`endpoints/model.py`의 "원격 모델 처리" 분기)
  GPU도 다운로드도 필요 없습니다.
- `chatbot.text_model` / `chatbot.embedding_model`(`configs/config.yaml`)은 이 테이블의
  **등록명**을 가리킵니다. 모델 ID가 아닙니다.

### GPU 없이 Gemma API 쓰기

코드 수정 없이 설정만으로 됩니다.

1. `POST /v1/model` — `provider=gemini`, `model_id=gemma-4-31b-it`, `api_key=<AI Studio 키>`
2. `configs/config.yaml`의 `chatbot.text_model`을 그 등록명으로

**임베딩은 반드시 로컬로 두세요.** Gemma API에는 임베딩 엔드포인트가 없고, Gemini 임베딩은
3072차원이라 pgvector 인덱스 상한(2000)을 넘습니다. (`app/utils/database.py` 주석 참고)
현재는 `nlpai-lab/KURE-v1`(1024차원)을 컨테이너 안에서 CPU로 돌립니다.
`chatbot.embedding_dim`은 `faq_embeddings.embedding` 컬럼·HNSW 인덱스와 고정 연결되어 있어
바꾸면 컬럼/인덱스 재생성 + 전량 재색인이 필요합니다.

## LLM 호출은 반드시 app/utils/litellm.py를 거친다

`litellm.acompletion`을 직접 부르지 말고 기존 헬퍼를 쓰세요. 사용량·비용 집계가 여기 묶여 있습니다.

- `stream_chat_completion()` — 스트리밍. 텍스트 델타만 yield합니다.
  **추론(thinking) 델타는 의도적으로 걸러냅니다.** Gemma는 thinking을 끌 수 없어 항상 들어오는데,
  이걸 흘려보내면 챗봇이 사고 과정을 답변으로 출력합니다.
- `extract_token_counts(usage)` — `(입력, 캐시된 입력, 출력, 추론)` 토큰을 반환합니다.
  제공자가 추론 토큰을 어떻게 보고하든(포함/제외/미보고) 출력 토큰에 포함된 값으로 맞춰 줍니다.
  usage를 직접 뜯지 말고 이 함수를 쓰세요.
- `save_usage()` — 대시보드 집계용. 새 LLM 호출 경로를 만들면 여기도 연결해야
  `/v1/dashboard/stats`와 비용 계산이 어긋나지 않습니다.

## chat_graph.py

챗봇의 핵심입니다. 손대기 전에 읽어야 하는 파일.

- **프롬프트 길이 예산**이 `configs/config.yaml`에 있습니다.
  (`evidence_max_chars`, `history_max_chars`, `query_max_chars`, `source_content_max_chars`)
  합계가 모델 컨텍스트 상한을 넘으면 응답이 400으로 실패하고 미응답 문구로 대체됩니다.
  이력서 전문을 붙여넣는 사용자가 실제로 있어서 들어간 장치입니다.
- **의도 분류**(`ChatIntent`)가 라우팅 기준입니다. `academic`/`career`/`personal`/`document`/
  `emotion`/`small_talk`/`abuse`/`unknown`. `retrieval_log.detected_intent`에 기록됩니다.
- **`emotion`은 정서 지원 경로**입니다. 학생이 힘들다고 털어놓는 발화는 검색 없이 바로
  `generate`로 갑니다. 검색을 태우면 근거가 안 잡혀 "정보를 찾을 수 없습니다"로 끊깁니다.
  위기 신호 시 안내할 상담 창구는 `chatbot.counseling_contact`(설정)에서 옵니다.
  `chatbot.abuse_keywords`에 "죽겠다" 같은 말을 넣으면 이 경로가 사전 필터에 먹혀 막힙니다.
- **세션 요약**(`summary`)이 장기 맥락을 담습니다. 매 턴이 아니라
  `chatbot.summary_trigger_count`마다 갱신됩니다.
- 보조 LLM 호출(의도 분류·요약)은 `_complete()`를 씁니다. 스트리밍이 아니고,
  반환값에서 사고 과정을 제외한 `content`만 돌려줍니다.
- **문서 파싱은 로컬**입니다 (`docs/local-document-parsing.md`). 외부 Doc Parser는 쓰지 않습니다.
  HWP는 rhwp CLI → PDF → pypdfium2, 챗봇은 텍스트+페이지/표 이미지를 멀티모달로 넣습니다.
  학칙 인제스트만 기존 pyhwp(`hwp_extractor`)를 유지합니다.

## 하지 말 것

- `litellm.acompletion` 직접 호출 (사용량 집계 누락)
- 모델 ID 하드코딩 (DB 등록으로 바뀌어야 함)
- `chatbot.embedding_dim` 변경 (인덱스·재색인 동반)
- 시스템 프롬프트에서 "근거 없으면 지어내지 말 것" 지시 제거 (KAI-REQ-040 미응답 처리)

## 기동

```bash
cp configs/config.template.yaml configs/config.yaml   # 값 채우기
cp configs/.env.template configs/.env
docker compose up -d --build
```

서비스: `kmu-ai-api`, `frontend`(nginx 정적 서빙), `postgres`, `redis`,
`keycloak` + `keycloak-postgres`(전용 DB, 앱 DB와 별개)
