# 배포와 버전 추적

> 작성일 2026-08-20. 대상: `kmu-ai` 개발(ys) · 운영(계명대 GPU 서버) 배포 체계.
> 서버 주소·계정·비밀번호는 이 문서에 없다. 리포가 공개 저장소라서다.
> 그 정보는 리포 밖 `DEPLOYMENT_LOG_KMU.md` / `SERVER_TRANSFER_KMU.md` 에 있다.
> (둘 다 `.git/info/exclude` 로 빠져 있다)

## 1. 왜 이 문서가 필요한가

서버가 셋이고 역할이 다른데, **어디에 무엇이 올라가 있는지 알 방법이 없었다.**
2026-08-19~20 사이에 겪은 사고가 전부 이 한 가지에서 나왔다.

| 사고 | 원인 |
| --- | --- |
| 운영 vLLM 이 gemma-4-31B-it 를 못 띄움 | 이미지가 `:latest` 라 어느 빌드인지 알 수 없었다. 컨테이너에 들어가 `vllm.__version__` 을 찍어 보고서야 0.27.0 인 걸 알았다 |
| 개발 챗봇이 모든 질문에 미응답 | 등록된 `model_id` 가 `gemma-4-31b-it` 인데 노드는 `-awq` 를 서빙 중이었다. 응답도 의도 분류도 404 로 죽었는데 아무도 몰랐다 |
| 규정 181건 중 45건만 색인된 채 8일 방치 | 재색인이 도중에 죽었는데 그걸 되돌아보는 잡이 없었다 |
| 운영 `docker-compose.yml` 이 두 브랜치 어디에도 없음 | 서버에서 직접 고쳤고 기록이 남지 않았다 |

## 2. 서버 세 대

| 역할 | 이름 | 성격 |
| --- | --- | --- |
| 개발 | `ys` | 인터넷·Docker 있음. **개발은 여기서만 한다.** 이미지 빌드도 여기가 정석 |
| 중계 | 담당자 PC (Windows) | **양쪽에 닿는 유일한 지점.** 배포는 여기서만 나간다 |
| 운영 | 계명대 GPU 서버 | 완전 폐쇄망. HIWARE 게이트웨이 터널로만 접근. GPU 서빙(H200 ×2) |

운영은 **인터넷이 없다.** `git clone`/`pull` 도, `pip install` 도, `docker pull` 도 안 된다.
그래서 모든 반입이 "중계 PC 를 거친 파일 전송"이다. 이 제약이 아래 모든 설계를 규정한다.

### HIWARE 제약 (자동화할 때 반드시 지킬 것)

- 비밀번호 인증만 가능. **공개키 인증은 게이트웨이가 지원하지 않는다.** (확인함)
- `plink` 호출 하나 = 인증 하나. **3초 간격 폴링으로 계정이 차단된 적이 있다.**
  폴링은 30초 이상, 가능하면 45초.
- 연결 공유(`plink -share`)는 일반 명령 채널엔 되지만 **`pscp` 파일 전송·X11 과 겹치면
  연결이 죽는다.** 파일 전송은 전용 연결로 할 것.
- 출력이 몇 초 멎으면 **종료코드 0 에 빈 출력**으로 조용히 끊는다.
  성공처럼 보이는 실패가 흔하므로, 긴 명령은 서버에서 detach 로 돌리고 결과 파일을 폴링한다.
- `plink` 가 명령줄의 한글을 CP949 로 훼손한다. **원격 명령은 ASCII 만 쓰거나 base64 로 실을 것.**
- SSH 포트포워딩(`direct-tcpip`)은 게이트웨이가 거부한다. `plink -L` 터널은 불가능하다.

## 3. 버전 추적 시스템 (`ops/versions/`)

```
ops/versions/dev.json     ys 의 Claude 만 쓴다
ops/versions/prod.json    중계 PC 의 Claude 만 쓴다 (expected_diff 도 여기)
scripts/version_probe.py  대상 호스트에서 실행. 상태를 JSON 한 덩어리로 뽑는다
scripts/version_log.py    report / probe / record / audit / commit
.claude/settings.json     SessionStart 훅
.claude/commands/version-check.md
ops/local.env             접속 자격증명. 커밋 안 됨 (.git/info/exclude)
```

### 파일을 환경별로 나눈 이유

하나로 합치면 두 머신이 같은 파일을 써서 git 충돌이 상시 발생한다. 환경별로 쪼개면
**서로 다른 파일만 건드리므로 충돌이 구조적으로 불가능**하다. 이력은 파일 안에 쌓지 않고
`git log -p ops/versions/prod.json` 이 담당한다.

### 무엇을 비교하는가

| 비교함 | 비교 안 함 (조회용) |
| --- | --- |
| `code.commit`, `code.dirty_counts`, `code.dirty_watch` | `code.dirty_files` 전체 목록 |
| `constants.*` (INGEST/EMBEDDING/RHWP_VERSION 등) | `captured_at` |
| `config.files.*` (파일 해시), `config.keys.*` (개별 키) | `runtime.*` |
| `containers.*` (이미지 ID·라벨·상태), `images.*` | `*.created`, `*.started_at` |
| `db.*` (카운트·모델·스키마 지문) | |

**태그로는 아무것도 알 수 없다.** compose 가 전부 `:latest` 를 쓰고 운영 이미지는
레지스트리를 거치지 않고 `docker save`/`load` 로 손으로 나르기 때문이다.

신원은 **OCI 라벨 `org.opencontainers.image.revision`**(전체 커밋 sha)로 본다.
원래는 이미지 ID 를 쓰려 했으나 도커 버전이 다르면 보존되지 않는다 — 자세한 실측은
[§4 신원은 이미지 ID 가 아니라 OCI 라벨로 본다](#신원은-이미지-id-가-아니라-oci-라벨로-본다).

`image_id` 는 **같은 호스트 안에서만** 의미가 있다. 컨테이너가 *실제로 돌리는* ID 와
태그가 가리키는 ID 가 다르면 **"빌드는 했는데 재시작을 안 한"** 상태다.

### 의도된 차이 (`expected_diff`)

운영과 개발은 원래 다른 부분이 많다(운영만 로컬 GPU 모델, 운영만 규정 181건, 워커 수 등).
그걸 매번 경고하면 사람이 곧 무시하게 된다. `prod.json` 의 `expected_diff` 에 선언하면 조용해진다.

| 판정 | 뜻 |
| --- | --- |
| `DRIFT` | 조치가 필요한 차이 |
| `EXPECTED` | 선언된 차이. 조용히 넘어감 |
| `STALE-RULE` | **선언된 값과 실제가 어긋남.** 규칙이 새 드리프트를 가리고 있다는 신호 |

- `mode: "pin"` — 값까지 못 박는다. 실제가 달라지면 `STALE-RULE` 로 다시 시끄러워진다. **권장.**
- `mode: "ignore"` — 경로 전체를 제외. 값이 정상적으로 계속 변하는 것에만 쓴다(세션 수 등).
- 값을 선언하지 않은 `pin` 은 사실상 `ignore` 이므로 그렇다고 경고한다.
- `todo` 를 달아 두면 억제가 영구화되지 않는다.

### 공개 리포에 비밀이 들어가지 않게

설정값은 화이트리스트(`REDACTION`)에 있는 키만, 세 등급으로만 기록한다.

| 등급 | 기록 | 대상 |
| --- | --- | --- |
| `plain` | 값 그대로 | 이미 `config.template.yaml`·compose 에 공개된 것 (모델 등록명, 임계값, 불리언) |
| `shape` | 분류 토큰 7종 (`empty`, `container:<이름>`, `localhost`, `private-ip`, `public-ip`, `public-host`, `name`) | 호스트·URL |
| `presence` | `set`/`unset` | 비밀번호·시크릿·API 키 |

**저엔트로피 값은 해시도 남기지 않는다.** IPv4 의 SHA-256 은 전수(2³²)로 수 초 만에
역산되고, 비밀번호 해시는 공개 저장소에 놓인 오프라인 크래킹 표적이 된다. 그래서 호스트는
해시가 아니라 `shape` 다. 반면 **파일 전체 해시**는 원본이 크고 추측 불가라 안전하다.

`shape` 는 유출이 없으면서 진단력도 있다 — 실제 운영 장애 원인이 `redis.host` 가 엉뚱한
컨테이너를 가리킨 것이었는데, `container:kmu-ai-redis` vs `container:pet-pass-one-ai-redis`
면 한눈에 보인다.

**`record` 는 쓰기 전에 정규식으로 검사해서 걸리면 거부한다.** 거부되면 우회하지 말고
`version_probe.py` 의 `REDACTION` 을 고칠 것.

### 언제 도는가

- **세션 시작 훅** — 기록된 파일만 읽는다. **SSH 를 절대 쓰지 않는다.** 매 세션 인증이
  발생하면 HIWARE 차단을 부르기 때문이다. 훅이 부르는 `report`(--strict 없이)는 차이가 있어도 종료코드 0 이라
  세션 시작을 막지 않는다. `--strict` 를 주면 1/2 를 돌려주므로 훅에는 쓰지 않는다.
- **`/version-check`** — 실제로 서버에 붙어 수집하고 기록을 갱신한다.
  운영 수집은 HIWARE 터널이 열려 있어야 하고, 45초 폴링이라 최대 6분 걸린다(인증 5~12회 — 확인·전송·정리·기동 4회 + 폴링 최대 8회).

> **주의**: 운영을 타이머로 주기 폴링하지 말 것. 차단 사고를 겪은 뒤라 위험하다.
> 운영은 사람이 터널을 연 상태에서 `/version-check` 를 칠 때만 건드린다.

## 4. 배포 절차

운영은 인터넷이 없어 `docker pull` 도 `docker compose build` 도 못 한다.
**빌드는 인터넷이 있는 곳에서 하고, 이미지를 파일로 날라서 `docker load` 한다.**

```
1. 빌드 컨텍스트   git -c core.autocrlf=false -c core.eol=lf archive HEAD | tar -x -C <dir>
                   ★ 그냥 `git archive` 는 LF 를 보장하지 않는다. core.autocrlf=true 면
                     체크아웃과 같은 변환을 적용해 CRLF 로 나온다. (2026-08-20 에 이걸로
                     운영을 크래시 루프에 빠뜨렸다)
2. 검증           CR 바이트를 직접 센다. 0 이어야 한다:
                     tr -cd "\r" < <dir>/scripts/launch.sh | wc -c
                   ★ grep 으로 CR 을 찾는 방식은 셸 이스케이프에 따라 조용히 오답을 낸다.
                     실제로 그 검증이 통과해서 CRLF 이미지를 운영에 배포했다.
                     작업 트리(CR>0)와 대비해 확인하면 확실하다.
3. 빌드           docker build --provenance=false --sbom=false ...
                   OCI 라벨에 revision(전체 sha)과 version(YYYYMMDD-짧은sha)을 굽는다.
4. 기동 확인       docker run 으로 실제로 띄워 본다. ASCII 로고가 뜨면 ENTRYPOINT 정상.
                   (설정 파일 오류는 볼륨 없이 단독 실행해서 나는 것이라 무시)
5. 저장           docker save | gzip   (1.6GB → 557MB)
6. 전송           HIWARE 터널로 pscp. ★ -share 금지 (연결이 죽는다)
7. 적재           gunzip | docker load → 같은 버전 태그로 재태그
                   ★ 적재 후 OCI 라벨의 revision 이 기대한 커밋과 같은지 확인하고 진행할 것
8. 재시작         docker compose up -d --no-build --force-recreate <서비스>
9. 검증           status/restarts, /health, 실제 질의
10. 기록          /version-check 로 재수집 후 커밋
```

**롤백 준비**: 배포 전 이미지 ID를 적어 둔다. 크래시 루프가 나면
`docker tag <이전ID> jdone/kmu-ai-api:latest && docker compose up -d --force-recreate` 로
1분 안에 되돌아간다.

**앱 코드는 이미지에 구워져 있다.** compose 가 마운트하는 것은 `static`·`configs`·
`resources`·`logs` 뿐이라, 코드 한 줄을 바꿔도 이미지를 다시 만들어야 한다.
그래서 **특정 커밋만 골라 배포할 수 없다** — 이미지는 그 시점 브랜치 전체를 담는다.

### 신원은 이미지 ID 가 아니라 **OCI 라벨**로 본다

원래 이미지 ID 로 비교하려 했으나, 실측 결과 **도커 버전이 다르면 보존되지 않는다.**

| 경로 | 결과 |
| --- | --- |
| 로컬 26.0.0 → 로컬 26.0.0 왕복 | 보존 (`19242ae9` 동일) |
| 로컬 26.0.0 → 운영 29.7.2 | **불일치** (`19242ae9` → `02107de3`) |
| BuildKit 어테스테이션 포함 빌드 | **불일치** (`--provenance=false` 로 해결) |

적재하는 데몬이 config 를 재정규화하면 ID가 바뀐다. 그래서 신원은
`org.opencontainers.image.revision`(전체 커밋 sha) 라벨로 본다. 라벨은 config 안에 있어
버전이 달라도 그대로 남고, 커밋까지 담고 있어 이미지 ID보다 낫다. 실제로 양쪽에서
동일함을 확인했다.

`image_id` 는 **같은 호스트 안에서만** 의미가 있다 —
`containers.X.image_id != images.Y.id` 이면 "빌드는 했는데 재시작을 안 한" 상태다.

## 5. Jenkins CI/CD 현황

`Jenkinsfile` 은 **백엔드 이미지 빌드·푸시까지만** 한다. 배포 단계가 없다.

```groovy
IMAGE_TAG  = "harbor.jdone.co.kr/backend/kmu-ai-api:${BUILD_DATE}-${GIT_HASH}"
LATEST_TAG = "harbor.jdone.co.kr/backend/kmu-ai-api:latest"   // main/master 에서만
```

OCI 라벨로 빌드 메타데이터를 굽는다 — `revision`(전체 sha), `version`(`YYYYMMDD-sha`),
`created`, `source`. **지금 존재하는 유일한 기계 판독 버전 식별자다.**

### 알려진 간극

| 문제 | 내용 |
| --- | --- |
| **이름 불일치** | Jenkins 는 `harbor.jdone.co.kr/backend/kmu-ai-api` 로 푸시하는데 `docker-compose.yml` 은 `jdone/kmu-ai-api` 를 쓴다. 태그 체계와 실행 설정이 끊겨 있다 |
| **라벨을 아무도 안 읽음** | OCI 라벨은 굽지만 앱이 런타임에 노출하지 않는다. `/health` 는 `{"status":"ok"}` 뿐이고 `pyproject.toml` 버전은 `0.1.0` 고정이라 CI 가 손대지 않는다 |
| **폐쇄망까지 못 감** | Harbor 는 운영에서 닿지 않는다. 운영 이미지는 손으로 나른 `docker save` 산출물이라 레지스트리 다이제스트가 없다 |
| **프론트엔드 미빌드** | Jenkins 는 백엔드만 빌드한다 |
| **배포 단계 없음** | `--push` 로 끝난다. ssh/compose/k8s 단계가 없다 |

### 개선하려면 (아직 안 함)

1. `docker-compose.yml` 의 이미지 이름을 Jenkins 것과 맞추고 태그를 변수화
   (`${KMU_AI_VERSION:-latest}`)
2. `Dockerfile` 에 `ARG GIT_COMMIT`/`IMAGE_VERSION` → `ENV` 로 승격, `/health` 가 이를 반환
   (`include_in_schema=False` 라 공개 계약이 바뀌지 않는다)
3. `RHWP_VERSION` 이 `Dockerfile` ARG 와 `app/utils/local_doc_parse.py` 에 **두 군데**
   손으로 동기화되고 있다. 파이썬 쪽을 `os.getenv` 로 바꾸면 어긋날 수가 없어진다

## 6. 알려진 문제

### 운영 리포의 줄끝이 전부 CRLF

Windows 에서 `tar` 로 옮기며 줄끝이 딸려 갔다. 리포는 LF 로 보관하므로 **내용이 같아도
286개 파일이 "수정됨"으로 잡힌다.** (미추적 3건을 더해 `report` 는 `+289수정` 으로 표시하니
두 숫자를 섞어 쓰지 말 것) `LICENSE` 에 CR 이 35개 있는데 바이트 내용은 개발과 동일하고,
`Dockerfile` 은 `git diff --ignore-all-space` 로 실질 변경이 0줄이다.

영향:
- 운영에서 `git status` 가 사실상 못 쓰는 상태다. **진짜 수정이 묻힌다.**
- 나중에 운영에서 `git pull` 하면 전 파일이 충돌한다.

추적 시스템은 이걸 `expected_diff` 로 억제하되 개수를 못 박아 두었다(숫자가 변하면 진짜
수정이므로 다시 잡힌다). **근본 해결은 줄끝 정규화다.** 정규화하면 그 규칙들을 지울 것.

### 그 밖

- **마이그레이션 원장이 없다.** `migrations/<날짜>/*.sql` 을 손으로 돌린다. peewee 의
  `create_tables(safe=True)` 는 빠진 테이블은 만들지만 **빠진 컬럼은 안 만든다.**
  추적 시스템은 `information_schema.columns` 지문으로 스키마 차이를 잡는다.
- **모델은 DB 등록이라 코드로 알 수 없다.** 같은 커밋이어도 `models` 테이블이 다르면
  동작이 다르다. 그래서 `db.models` 를 이름 키 사전으로 기록한다.

## 7. 모델 서빙 용량 (H200 ×2)

`max-model-len` 을 32,768 → 65,536 으로 올렸다. 프롬프트 예산을 키우면
(evidence 20,000 + history 6,000 + query 8,000 = 34,000자) 한국어는 글자당 토큰 비율이
높아 32,768 상한을 넘길 수 있어서다.

vLLM 이 기동하며 실제로 재는 값이 로그에 남는다. **이게 권위 있는 수치다.**

```
GPU KV cache size: 609,100 tokens
Maximum concurrency for 65,536 tokens per request: 9.29x
```

동시 처리 수 = **609,100 ÷ 요청당 토큰 수**.

| 요청 크기 | 동시 처리 | 해당하는 상황 |
| --- | --- | --- |
| 65,536 토큰 (상한) | **9.3** | 컨텍스트를 꽉 채운 요청. 실제로는 거의 없다 |
| 34,000 토큰 (예산 상한) | **17.9** | 근거 20,000자를 다 채운 최악의 질의 |
| 8,000 토큰 | **76** | 근거가 많이 잡히는 일반 규정 질의 |
| 2,000 토큰 | **304** | 짧은 질의·잡담 |

실사용은 대부분 세 번째·네 번째 줄이므로 **수십 명 동시 사용은 여유롭다.**
다만 이 수치는 KV 캐시 상한일 뿐이고, 실제 처리량은 GPU 연산과
`--max-num-seqs` 설정에도 걸린다.

### 왜 609,100 인가

이 모델은 레이어마다 어텐션 구조가 다르다.

| 레이어 | 개수 | head_dim | KV heads | 비고 |
| --- | --- | --- | --- | --- |
| sliding_attention | 50 | 256 | 16 | 윈도우 1,024 로 잘려 시퀀스당 비용이 고정 |
| full_attention | 10 | **512** | 4 | `attention_k_eq_v=true` — K 를 V 로 재사용 |

토큰당 비용을 지배하는 것은 **full_attention 10개 레이어**다. sliding 레이어는 윈도우가
1,024 라 시퀀스가 길어져도 더 늘지 않는다. 여기에 텐서 병렬 2로 KV 헤드가 두 GPU 에
쪼개지고, `gpu_memory_utilization=0.85` 에서 가중치(62GB)를 뺀 나머지가 KV 캐시로 간다.

> 손으로 계산한 값은 vLLM 수치와 정확히 맞지 않았다. 블록 할당·예약 오버헤드가 더 있어서다.
> **용량 판단은 위 로그 값을 기준으로 하고, 이 표는 어느 항목이 비용을 지배하는지 이해하는
> 용도로만 쓴다.** 설정을 바꾼 뒤에는 로그를 다시 읽어 확인할 것.


## 8. 2026-08-20 기준 드리프트

배포 후 상태다.

```
dev  a35b8f6  clean
prod a35b8f6  +4수정(운영 전용 파일)
DRIFT 0 / STALE-RULE 0 / 선언된 차이 23
```

`f073523`(규정 재색인 잡 + 학생 말투 동의어 확장)이 운영에 반영됐고,
`evidence_max_chars` 를 20,000 으로 올렸다. 운영 리포의 CRLF 286건은 git bundle 로
리포를 갱신하며 해소됐다(수정 파일 286 → 0).

운영에만 남은 미추적 파일 4건은 `docker-compose.override.yml`(`/data/models` 마운트)과
설정 백업 3건이다. override 로 옮긴 덕분에 앞으로 `git reset` 에 지워지지 않는다.

## 관련 문서

- [`version-drift-tracking.md`](version-drift-tracking.md) — 이 체계를 왜 이렇게 만들었는지 (배경·설계 판단·한계)
- [`local-document-parsing.md`](local-document-parsing.md) — HWP/PDF 로컬 파싱
- [`rag-management-api.md`](rag-management-api.md) — 지식베이스 관리 API
- [`regulation_rag_eval.md`](regulation_rag_eval.md) — 규정 검색 평가
- `CLAUDE.md` — 코드베이스 불변 규칙. 버전 추적 요약도 여기 있다
