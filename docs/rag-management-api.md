# RAG Management API 명세

다른 프로젝트에서 **RAG 관리** 화면(`/admin/rag`)을 동일하게 구현할 때 필요한 API 전체 명세입니다.

- Base path: `/v1/rag`
- Content-Type: `application/json` (업로드만 `multipart/form-data`)
- Auth: `Authorization: Bearer <Keycloak JWT | jd-API Key>`
- 권한: 관리자 역할 (`admin_roles`, 보통 `admin`)
- 공통 목록 래퍼: `{ total_pages, total_count, items }`
- 벡터 저장소: PostgreSQL **pgvector** (Pinecone 아님)

관련 화면: `frontend/src/pages/admin/AdminRagPage.jsx`  
관련 클라이언트: `frontend/src/lib/endpoints.js` → `rag`

---

## 화면 기능 ↔ API

| UI | Method | Path |
|---|---|---|
| 목록·검색·필터·페이지네이션 | `GET` | `/v1/rag/items` |
| 행 클릭 → 우측 상세 패널 | `GET` | `/v1/rag/items/{name}` |
| 상세 패널 설정 저장 (`chunk_size` 등) | `PATCH` | `/v1/rag/items/{name}` |
| 활성 토글 | `PATCH` | `/v1/rag/items/{name}/active` |
| 파일 업로드 모달 | `POST` | `/v1/rag/items/{name}/upload` |
| 로그 보기 모달 | `GET` | `/v1/rag/items/{name}/logs` |
| 일괄/즉시 동기화 | `POST` | `/v1/rag/sync` |
| 일괄/단건 삭제 | `DELETE` | `/v1/rag/items` |
| 내보내기(JSON 다운로드) | `GET` | `/v1/rag/export` |

---

## Enum

### `embedding_status` (RagEmbeddingStatus)

| 값 | 라벨 | UI |
|---|---|---|
| `syncing` | 동기화 중 | blue badge |
| `success` | 동기화 완료 | blue badge |
| `error` | 에러 | red badge |

### `source_type` (관리형 KB만)

| 값 | 의미 |
|---|---|
| `faq` | FAQ |
| `regulation` | 학칙·규정 |
| `notice` | 공지 (미사용) |
| `document` | 업로드 문서 (미사용) |

### `last_job_status` / ingestion

| 값 | 의미 |
|---|---|
| `pending` | 대기 |
| `running` | 실행 중 |
| `completed` | 완료 |
| `failed` | 실패 |

### 관리형 지식베이스

| `name` | 표시명 | 동기화 | 문서 업로드 |
|---|---|---|---|
| `kmu_faq_knowledge` | FAQ 지식베이스 | O (`source_type=faq`) | X (`400`) |
| `kmu_regulations` | 대학 규정집 | O (`source_type=regulation`) | O |

---

## 공통 스키마: `RagItemInfo`

목록·상세·내보내기의 기본 항목입니다.

```json
{
  "name": "kmu_regulations",
  "display_name": "대학 규정집",
  "display_name_en": "Uni Regulations",
  "bot_label": "학생 봇",
  "bot_label_en": "Student Bot",
  "source_type": "regulation",
  "document_count": 181,
  "chunk_count": 5359,
  "embedding_status": "success",
  "embedding_status_label": "동기화 완료",
  "last_synced_at": "2026-07-27T18:30:00+09:00",
  "is_active": true,
  "vector_db": "pgvector",
  "embedding_model": "text-embedding-3-small",
  "vector_size": 1024,
  "description": null,
  "is_system": true,
  "chunk_size": 1000,
  "chunk_overlap": 100,
  "top_k": 5,
  "similarity_threshold": 0.35,
  "created_at": "2026-07-01T09:00:00+09:00",
  "updated_at": "2026-07-27T18:30:00+09:00"
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `name` | string | 컬렉션 PK (경로 파라미터로도 사용) |
| `display_name` | string | 한글 표시명 |
| `display_name_en` | string\|null | 영문 표시명 |
| `bot_label` | string\|null | 봇 표시명 |
| `bot_label_en` | string\|null | 봇 영문명 |
| `source_type` | enum\|null | 관리형만 값 있음 |
| `document_count` | int | 문서/FAQ 수 |
| `chunk_count` | int | 청크/임베딩 수 |
| `embedding_status` | enum | `syncing`/`success`/`error` |
| `embedding_status_label` | string | 한글 라벨 |
| `last_synced_at` | string\|null | ISO datetime |
| `is_active` | bool | 활성 토글 |
| `vector_db` | string | 항상 `pgvector` |
| `embedding_model` | string | 임베딩 모델명 |
| `vector_size` | int | 벡터 차원 |
| `description` | string\|null | 설명 |
| `is_system` | bool | 시스템 KB |
| `chunk_size` | int | 청킹 크기 (기본 1000) |
| `chunk_overlap` | int | 오버랩 (기본 100) |
| `top_k` | int | 검색 상위 K (기본 5) |
| `similarity_threshold` | float | 최소 유사도 (기본 0.35) |
| `created_at` | string | 생성 시각 |
| `updated_at` | string | 수정 시각 |

### `RagItemDetailResponse` = `RagItemInfo` +

```json
{
  "recent_error": null,
  "processing_document_count": 0,
  "error_document_count": 0,
  "last_job_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "last_job_status": "completed"
}
```

---

## 1. 목록 조회

```http
GET /v1/rag/items?page=1&count=25&search=규정&status=success&is_active=true&date=2026.07&include_system=true
```

### Query

| 파라미터 | 타입 | 기본 | 설명 |
|---|---|---|---|
| `page` | int (≥1) | `1` | 페이지 |
| `count` | int (0–100) | `25` | 페이지당 수. `0`이면 전체 |
| `search` | string | - | 이름·설명 검색 |
| `status` | enum | - | `syncing` \| `success` \| `error` |
| `is_active` | bool | - | 활성 여부 |
| `date` | string | - | `YYYY.MM` (updated_at 월 필터) |
| `include_system` | bool | `true` | 시스템 KB 포함 |

### Response `200`

```json
{
  "total_pages": 1,
  "total_count": 2,
  "items": [ /* RagItemInfo[] */ ]
}
```

### 화면 사용

- 검색 입력 debounce → `search`
- 드롭다운: 전체/활성/비활성 → `is_active`
- 드롭다운: 상태 → `status`
- month input → `date` (`YYYY-MM` → `YYYY.MM` 변환)
- 페이지당 10/25/50 → `count`

---

## 2. 상세 조회

```http
GET /v1/rag/items/{name}
```

### Path

| 파라미터 | 설명 |
|---|---|
| `name` | 컬렉션 이름 (URL encode) |

### Response `200`

`RagItemDetailResponse`

### Error

| 코드 | 조건 |
|---|---|
| `404` | 지식베이스 없음 |

### 화면 사용

행 클릭 시 우측 패널. 통계·상태·`chunk_size`/`top_k`/`similarity_threshold` 폼 초기값.

---

## 3. 메타·검색/청킹 설정 수정

```http
PATCH /v1/rag/items/{name}
Content-Type: application/json
```

### Body (모두 optional, 보낸 필드만 변경)

```json
{
  "description": "학칙·규정 HWP 코퍼스",
  "is_active": true,
  "chunk_size": 1000,
  "chunk_overlap": 100,
  "top_k": 5,
  "similarity_threshold": 0.35
}
```

| 필드 | 제약 |
|---|---|
| `description` | max 1024 |
| `chunk_size` | 100–10000 |
| `chunk_overlap` | 0–5000 |
| `top_k` | 1–50 |
| `similarity_threshold` | 0.0–1.0 |

### Response `200`

`RagItemDetailResponse`

### 화면 사용

상세 패널 **설정 저장** 버튼 → `chunk_size`, `chunk_overlap`, `top_k`, `similarity_threshold`만 전송.

---

## 4. 활성 토글

```http
PATCH /v1/rag/items/{name}/active
Content-Type: application/json

{ "is_active": false }
```

### Response `200`

`RagItemDetailResponse`

### 화면 사용

테이블 **활성** 스위치.

---

## 5. 문서 업로드

```http
POST /v1/rag/items/{name}/upload
Content-Type: multipart/form-data
```

### Form fields

| 필드 | 타입 | 기본 | 설명 |
|---|---|---|---|
| `file` | file | 필수 | 업로드 파일 |
| `background` | bool | `true` | 백그라운드 임베딩 |
| `chunk_size` | int | 컬렉션 설정 | 생략 시 컬렉션값 |
| `chunk_overlap` | int | 컬렉션 설정 | 생략 시 컬렉션값 |

### Response `200`

```json
{
  "document_id": 12,
  "file_name": "규정.pdf",
  "collection_name": "kmu_regulations",
  "chunk_count": 0
}
```

`background=true`이면 `chunk_count`는 0일 수 있음 (처리 후 증가).

### Error

| 코드 | 조건 |
|---|---|
| `400` | FAQ KB(`kmu_faq_knowledge`) 업로드 시도 / 빈 파일 |
| `404` | 컬렉션 또는 임베딩 모델 없음 |

### 화면 사용

상단 **파일 업로드** 모달. FAQ는 선택 목록에서 제외.

---

## 6. 동기화 (단건·일괄)

```http
POST /v1/rag/sync
Content-Type: application/json

{
  "names": ["kmu_faq_knowledge", "kmu_regulations"],
  "force": false,
  "only_stale": true
}
```

| 필드 | 타입 | 기본 | 설명 |
|---|---|---|---|
| `names` | string[] (min 1) | 필수 | 컬렉션 이름 목록 |
| `force` | bool | `false` | 강제 재색인 |
| `only_stale` | bool | `true` | stale 등만 (FAQ) |

### Response `200` (부분 실패도 200)

```json
{
  "message": "1/2건 동기화를 시작했습니다.",
  "results": [
    {
      "name": "kmu_faq_knowledge",
      "success": true,
      "message": "FAQ 재색인 작업이 시작되었습니다.",
      "job_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    },
    {
      "name": "demo_docs",
      "success": false,
      "message": "업로드 문서 컬렉션은 원천 연계 재색인을 지원하지 않습니다. ...",
      "job_id": null
    }
  ]
}
```

### 화면 사용

- 상단 **동기화**: `["kmu_faq_knowledge", "kmu_regulations"]`
- **일괄 동기화** / 상세 **즉시 동기화**: 선택 `names`

진행 확인(선택): `GET /v1/ingestion/job/{job_id}`

---

## 7. 일괄·단건 삭제

```http
DELETE /v1/rag/items
Content-Type: application/json

{ "names": ["demo_docs", "old_kb"] }
```

단건도 `names: ["one"]`으로 동일 API.

### Response `200`

```json
{
  "message": "1/2건 삭제되었습니다.",
  "results": [
    { "name": "demo_docs", "success": true, "message": "삭제되었습니다." },
    {
      "name": "kmu_regulations",
      "success": false,
      "message": "시스템 관리형 지식베이스는 삭제할 수 없습니다."
    }
  ]
}
```

보호: `kmu_faq_knowledge`, `kmu_regulations`

### 화면 사용

행 삭제 아이콘 / **일괄 삭제** (confirm 후 호출).

---

## 8. 로그 조회

```http
GET /v1/rag/items/{name}/logs?page=1&count=20
```

### Response `200`

```json
{
  "total_pages": 1,
  "total_count": 1,
  "items": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "event_type": "ingestion_job",
      "status": "completed",
      "message": "success=180, failed=1, total=181",
      "created_at": "2026-07-25T10:00:00+09:00",
      "meta": {
        "ended_at": "2026-07-25T10:30:00+09:00",
        "source_type": "regulation"
      }
    }
  ]
}
```

| `event_type` | 의미 |
|---|---|
| `ingestion_job` | 관리형 KB 수집 이력 |
| `document_error` | 일반 컬렉션 오류 문서 |

### 화면 사용

상세 **로그 보기** 모달.

---

## 9. 내보내기

```http
GET /v1/rag/export?search=규정&status=success&is_active=true&include_system=true
```

필터는 목록과 동일(페이지 없이 전체). 응답 형식은 목록과 같음 (`count=0`과 동일).

### 화면 사용

**내보내기** → JSON 파일 다운로드.

---

## 공통 오류

| HTTP | 의미 |
|---|---|
| `401` | 미인증 |
| `403` | 관리자 권한 없음 |
| `404` | 항목 없음 (`{ message, target }`) |
| `400` | 잘못된 요청 (`{ message, target }`) |
| `422` | 스키마 검증 실패 |
| `500` | 서버 오류 |

---

## 마이그레이션 (참고)

```bash
docker exec -i kmu-ai-postgres psql -U jdone -d kmu_ai < migrations/2026-07-28/add_is_active_to_collections.sql
docker exec -i kmu-ai-postgres psql -U jdone -d kmu_ai < migrations/2026-07-28/add_rag_settings_to_collections.sql
```

| 컬럼 | 기본 |
|---|---|
| `collections.is_active` | `true` |
| `collections.chunk_size` | `1000` |
| `collections.chunk_overlap` | `100` |
| `collections.top_k` | `5` |
| `collections.similarity_threshold` | `0.35` |
