# 개인정보 마스킹(Masking) 규칙 API 명세

다른 프로젝트에서 **마스킹 관리** 화면(`/admin/masking`)을 동일하게 구현할 때 필요한 API 전체 명세입니다.

- Base path: `/v1/masking`
- Content-Type: `application/json`
- Auth: `Authorization: Bearer <Keycloak JWT | jd-API Key>`
- 권한: 관리자 역할 (`admin_roles`, 보통 `admin`)
- 공통 목록 래퍼: `{ total_pages, total_count, items }`

관련 화면: `frontend/src/pages/admin/AdminMaskingPage.jsx`  
관련 클라이언트: `frontend/src/lib/endpoints.js` → `masking`

런타임: 활성 규칙(`is_active=true`)은 챗봇 `/v1/chatbot/message`의 사용자·봇 메시지 저장 및 응답에 자동 적용됩니다. (FAQ 원문 CRUD는 미적용)

---

## 화면 기능 ↔ API

| UI | Method | Path |
|---|---|---|
| 목록·검색·상태/방식 필터·페이지네이션 | `GET` | `/v1/masking/items` |
| + 규칙 추가 / 저장(생성) | `POST` | `/v1/masking/items` |
| 행 수정 → 우측 패널 저장 | `PATCH` | `/v1/masking/items/{rule_id}` |
| 행 삭제 | `DELETE` | `/v1/masking/items/{rule_id}` |
| (선택) 상세 단건 조회 | `GET` | `/v1/masking/items/{rule_id}` |
| (선택) 미리보기 테스트 | `POST` | `/v1/masking/test` |

현재 관리 UI는 목록·생성·수정·삭제를 사용합니다. `GET 상세`와 `POST /test`는 API에 포함되어 있어 동일 화면 확장/재구현 시 사용하면 됩니다.

---

## Enum

### `target_field` (MaskingTargetField)

| 값 | 한글 (UI) |
|---|---|
| `student_id` | 학번 |
| `phone` | 전화번호 |
| `email` | 이메일 |
| `resident_id` | 주민등록번호 |
| `custom` | 기타 |

### `masking_method` (MaskingMethod)

| 값 | 한글 (UI) | 동작 요약 |
|---|---|---|
| `partial` | 부분 마스킹 | 앞·뒤 일부 보존, 중간 `replacement` (이메일은 로컬파트) |
| `middle` | 중간 마스킹 | 앞·뒤 일부 보존, 중간 치환 (전화 패턴에 적합) |
| `full` | 전체 마스킹 | 매칭 전체 → `replacement` |

---

## 공통 스키마: `MaskingRuleInfo`

```json
{
  "id": "a1000000-0000-4000-8000-000000000002",
  "name": "전화번호",
  "target_field": "phone",
  "regex_pattern": "01[0-9]-?\\d{3,4}-?\\d{4}",
  "masking_method": "middle",
  "replacement": "****",
  "description": "휴대폰 번호 중간 자리 마스킹",
  "is_active": true,
  "created_at": "2026-07-28T10:00:00+09:00",
  "updated_at": "2026-07-28T10:00:00+09:00"
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | UUID string | 규칙 ID |
| `name` | string | 규칙명 |
| `target_field` | enum | 대상 필드 분류 (UI 필터/표시용) |
| `regex_pattern` | string | 정규표현식 |
| `masking_method` | enum | 마스킹 방식 |
| `replacement` | string | 치환 문자열 (기본 `****`) |
| `description` | string\|null | 설명 |
| `is_active` | bool | 활성 (런타임 적용 대상) |
| `created_at` | string | 생성 시각 |
| `updated_at` | string | 수정 시각 |

자유 텍스트에는 **활성 규칙 전부**가 순서대로 적용됩니다. `target_field`는 분류·필터용 메타입니다.

---

## 1. 목록 조회

```http
GET /v1/masking/items?page=1&count=10&search=전화&is_active=true&masking_method=middle&target_field=phone
```

### Query

| 파라미터 | 타입 | 기본 | 설명 |
|---|---|---|---|
| `page` | int (≥1) | `1` | 페이지 |
| `count` | int (0–100) | `10` | 페이지당 수. `0`이면 전체 |
| `search` | string | - | 규칙명·설명·대상필드·정규식 검색 |
| `is_active` | bool | - | 활성 여부 |
| `masking_method` | enum | - | `partial` \| `middle` \| `full` |
| `target_field` | enum | - | `student_id` \| `phone` \| `email` \| `resident_id` \| `custom` |

정렬: `updated_at` 내림차순.

### Response `200`

```json
{
  "total_pages": 1,
  "total_count": 4,
  "items": [ /* MaskingRuleInfo[] */ ]
}
```

### 화면 사용

- 검색 placeholder: 「규칙명 또는 대상 필드 검색」→ `search`
- 전체 상태 / 활성 / 비활성 → `is_active`
- 전체 마스킹 방식 / partial·middle·full → `masking_method`
- 페이지당 10/25/50 → `count`
- 테이블 컬럼: ID(short)·규칙명·대상 필드·정규식·마스킹 방식·상태(활성 배지)·수정일·관리(수정/삭제)

---

## 2. 상세 조회

```http
GET /v1/masking/items/{rule_id}
```

### Path

| 파라미터 | 타입 | 설명 |
|---|---|---|
| `rule_id` | UUID | 규칙 ID |

### Response `200`

`MaskingRuleInfo`

### Error

| 코드 | 조건 |
|---|---|
| `404` | `{ "message": "마스킹 규칙을 찾을 수 없습니다.", "target": "rule_id=..." }` |

---

## 3. 생성

```http
POST /v1/masking/items
Content-Type: application/json
```

### Body

```json
{
  "name": "전화번호",
  "target_field": "phone",
  "regex_pattern": "01[0-9]-?\\d{3,4}-?\\d{4}",
  "masking_method": "middle",
  "replacement": "****",
  "description": "휴대폰 중간 자리 마스킹",
  "is_active": true
}
```

| 필드 | 필수 | 제약 |
|---|---|---|
| `name` | O | 1–255자 |
| `target_field` | O | enum |
| `regex_pattern` | O | 컴파일 가능해야 함 |
| `masking_method` | O | enum |
| `replacement` | X | max 64, 기본 `****` |
| `description` | X | |
| `is_active` | X | 기본 `true` |

### Response `200`

`MaskingRuleInfo` (생성된 행)

### Error

| 코드 | 조건 |
|---|---|
| `400` | 잘못된 정규식 (`target=regex_pattern`) |

### 화면 사용

**+ 규칙 추가** → 우측 패널 빈 폼 → **저장** → 본 API.

필수 UI 검증: 규칙명·정규표현식.

---

## 4. 수정

```http
PATCH /v1/masking/items/{rule_id}
Content-Type: application/json
```

### Body (보낸 필드만 변경)

```json
{
  "name": "전화번호",
  "target_field": "phone",
  "regex_pattern": "01[0-9]-?\\d{3,4}-?\\d{4}",
  "masking_method": "middle",
  "replacement": "****",
  "description": "휴대폰 중간 4자리 마스킹",
  "is_active": true
}
```

모든 필드 optional. `regex_pattern`이 포함되면 컴파일 검증.

### Response `200`

`MaskingRuleInfo`

### Error

| 코드 | 조건 |
|---|---|
| `400` | 잘못된 정규식 |
| `404` | 규칙 없음 |

### 화면 사용

행 **수정** → 우측 패널에 값 로드 → **저장**.  
활성 토글은 폼의 `is_active`로 함께 저장.

---

## 5. 삭제

```http
DELETE /v1/masking/items/{rule_id}
```

### Response `200`

```json
{
  "message": "마스킹 규칙이 삭제되었습니다."
}
```

### Error

| 코드 | 조건 |
|---|---|
| `404` | 규칙 없음 |

### 화면 사용

행 **삭제** (confirm 후 호출).

---

## 6. 마스킹 미리보기 (테스트)

```http
POST /v1/masking/test
Content-Type: application/json
```

### Body

```json
{
  "text": "연락처 010-1234-5678 입니다",
  "rule_id": null
}
```

| 필드 | 필수 | 설명 |
|---|---|---|
| `text` | O | 원문 (min 1) |
| `rule_id` | X | 지정 시 해당 규칙만. 생략 시 **활성 규칙 전부** |

### Response `200`

```json
{
  "original": "연락처 010-1234-5678 입니다",
  "masked": "연락처 010****5678 입니다",
  "applied_rule_count": 4
}
```

### Error

| 코드 | 조건 |
|---|---|
| `404` | `rule_id`가 있는데 규칙 없음 |

---

## 우측 패널 폼 필드 (재구현용)

| UI 라벨 | API 필드 | 입력 |
|---|---|---|
| 규칙명 * | `name` | text |
| 대상 필드 * | `target_field` | select (enum) |
| 정규표현식 * | `regex_pattern` | text (mono) |
| 마스킹 방식 * | `masking_method` | select |
| 치환 문자열 | `replacement` | text |
| 설명 | `description` | textarea |
| 상태 | `is_active` | toggle (활성/비활성) |
| 취소 / 저장 | — | 닫기 / create 또는 update |

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

## 시드 데이터 (참고)

마이그레이션 `migrations/2026-07-28/create_masking_rules.sql`에 기본 4건이 들어갑니다.

| 규칙명 | target_field | method | 예시 패턴 |
|---|---|---|---|
| 학번 | `student_id` | `partial` | `^\d{8}$` |
| 전화번호 | `phone` | `middle` | `01[0-9]-?\d{3,4}-?\d{4}` |
| 이메일 | `email` | `partial` | 이메일 정규식 |
| 주민등록번호 | `resident_id` | `full` | `\d{6}-?\d{7}` |

```bash
docker exec -i kmu-ai-postgres psql -U jdone -d kmu_ai < migrations/2026-07-28/create_masking_rules.sql
```
