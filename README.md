# AI Agent One - API

다양한 AI 모델을 한 곳에서 관리 및 사용할 수 있도록 하는 프로젝트입니다.

## 📚 주요 기능

1. 

## 🚀 빠른 시작

이 프로젝트를 실행하기 위해서는 Docker와 Docker Compose가 필요합니다.

1. 프로젝트 빌드

```bash
docker-compose build
```

2. 프로젝트 실행

```bash
docker-compose up -d
```

3. 로그 확인

```bash
docker-compose logs -f app
```

## ⚙️ 로컬 개발

1. 의존성 설치

```bash
uv sync
```

2. 프로젝트 실행

```bash
uv venv
sh ./scripts/launch_dev.sh
```

## 📚 프로젝트 구조

### 🏗️ 전체 구조 개요

```
ai-agent-one-api/
├── 📱 app/                       # 메인 애플리케이션 코드
├── ⚙️ configs/                   # 설정 파일들
├── 📜 scripts/                   # 실행 및 배포 스크립트
├── 🎨 static/                    # 정적 리소스 파일
├── 🧪 tests/                     # 테스트 코드
└── 📄 기타 파일들
```

#### 📱 애플리케이션 구조 (`app/`)

```
app/
├── 🌐 api/                        # REST API 관련
│   └── v1/                       # API 버전 1
│       ├── endpoints/            # API 엔드포인트 구현
│       │   └── store.py          # 매장 관리 API (CRUD 작업)
│       └── router.py             # API 라우팅 설정
│
├── ⚠️  exceptions/               # 예외 처리
│   └── api_exception.py          # API 예외 정의
│
├── 📊 models/                    # 데이터 모델 정의
│   ├── api/                      # API 관련 모델
│   │   ├── v1/
│   │   │   └── store.py          # 매장 관리 API 모델
│   │   ├── common.py             # 공통 API 모델 (페이징, 정렬 등)
│   │   └── exception.py          # 예외 응답 모델
│   ├── auth.py                   # 인증/인가 관련 모델
│   ├── config.py                 # 환경설정 모델
│   ├── database.py               # 데이터베이스 테이블 ORM 모델
│   └── db_item.py                # 데이터베이스 테이블 Pydantic 모델
│
├── 📤 payloads/                  # API 요청 페이로드
│   └── v1/
│       └── store.py              # 매장 관리 요청 데이터 구조
│
├── 📥 responses/                 # API 응답 모델
│   ├── v1/
│   │   └── store.py              # 매장 관리 응답 데이터 구조
│   ├── base.py                   # 기본 응답 구조 (성공/실패)
│   └── exception.py              # 에러 응답 구조
│
├── ⏰ scheduler/                 # 백그라운드 스케줄러
│   ├── jobs/                     # 작업 목록
│   │   ├── base.py               # 베이스 작업 (작업 생성)
│   │   └── dummy.py              # 더미 작업 (템플릿)
│   ├── scheduler.py              # 백그라운드 스케줄러
│   └── service.py                # 스케줄러 서비스 관리
│
├── 🛠️  utils/                    # 유틸리티 및 헬퍼 함수
│   ├── handlers/                 # 요청/응답 처리기
│   │   ├── exception_handler.py  # 전역 예외 처리기
│   │   └── http_handler.py       # HTTP 요청/응답 처리기
│   ├── auth.py                   # Keycloak 인증 유틸리티
│   ├── common.py                 # 공통 유틸리티 함수
│   ├── database.py               # 데이터베이스 연결 및 관리
│   ├── limiter.py                # API 요청 제한
│   ├── logger.py                 # 구조화된 로깅 시스템
│   ├── openapi.py                # OpenAPI Docs
│   └── redis.py                  # Redis 캐시 관리
│
├── config.py                     # 애플리케이션 전역 설정
└── main.py                       # FastAPI 애플리케이션 진입점
```

#### ⚙️ 설정 및 배포 (`configs/`, `scripts/`)

```
configs/
├── .env.template               # 환경변수 템플릿 (개발 가이드)
├── .env                        # 실제 환경변수 (보안 정보 포함)
├── config.template.yaml        # 애플리케이션 설정 템플릿
├── config.yaml                 # 실제 애플리케이션 설정
└── uvicorn_log_conf.yaml       # Uvicorn 웹서버 로그 설정

scripts/
├── launch.sh                   # 프로덕션 환경 실행 스크립트
└── launch_dev.sh               # 개발 환경 실행 (핫 리로드 지원)
```

#### 🎨 정적 파일 및 테스트 (`static/`, `tests/`)

```
static/
├── favicon.png                 # 웹사이트 파비콘
└── logo.png                    # 애플리케이션 로고

tests/
└── __init__.py                 # 테스트 패키지 초기화
```

#### 📄 루트 레벨 설정 파일

| 파일                 | 설명                                 |
| -------------------- | ------------------------------------ |
| `docker-compose.yml` | 다중 컨테이너 서비스 오케스트레이션  |
| `Dockerfile`         | Docker 컨테이너 이미지 빌드 설정     |
| `pyproject.toml`     | Python 프로젝트 메타데이터 및 의존성 |
| `README.md`          | 프로젝트 문서 및 사용 가이드         |
| `uv.lock`            | 정확한 의존성 버전 고정 파일         |
