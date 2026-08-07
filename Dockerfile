# Stage 1: requirements.txt 파일 생성
FROM python:3.13-slim AS build-stage

WORKDIR /tmp

# Git 설치 및 캐시 정리
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# pip 최신 버전으로 업데이트 및 uv 설치
RUN pip install --upgrade pip \
    && pip install --no-cache-dir uv

# requirements.txt 생성
COPY pyproject.toml poetry.lock* /tmp/
RUN uv pip compile pyproject.toml -o requirements.txt


# Stage 2: 애플리케이션 빌드 및 난독화 (선택적)
FROM python:3.13-slim AS builder-stage

ARG ENABLE_OBFUSCATION=false

WORKDIR /build

# 필요한 시스템 패키지만 설치하고 캐시 정리
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 의존성 설치 및 캐시 정리
COPY --from=build-stage /tmp/requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir uv
RUN uv venv
RUN uv pip install --no-cache -r ./requirements.txt && \
    rm -rf /root/.cache/pip

# 애플리케이션 복사
COPY . .

# TERM 환경 변수 설정
ENV TERM=xterm-256color

# 가상환경 동기화 (dev 의존성 제외)
RUN uv sync --frozen --no-dev

# PyArmor를 사용한 애플리케이션 난독화 (조건부 실행)
RUN if [ "$ENABLE_OBFUSCATION" = "true" ]; then \
    uv pip install --no-cache pyarmor==9.1.9 && \
    uv run pyarmor gen -O /build/obfuscated -r app; \
    else \
    mkdir -p /build/obfuscated && \
    cp -r app /build/obfuscated/; \
    fi


# Stage 3: 런타임 이미지
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 필요한 시스템 패키지만 설치하고 캐시 정리
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    git \
    fonts-noto-cjk \
    ca-certificates \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# HWP/HWPX → PDF (로컬 문서 파싱). https://github.com/edwardkim/rhwp
ARG RHWP_VERSION=v0.8.2
RUN curl -fsSL \
    "https://github.com/edwardkim/rhwp/releases/download/${RHWP_VERSION}/rhwp-${RHWP_VERSION}-linux-x86_64.tar.gz" \
    -o /tmp/rhwp.tar.gz \
    && tar -xzf /tmp/rhwp.tar.gz -C /tmp \
    && install -m 0755 /tmp/rhwp/rhwp /usr/local/bin/rhwp \
    && rm -rf /tmp/rhwp.tar.gz /tmp/rhwp \
    && rhwp --help >/dev/null
ENV RHWP_BIN=/usr/local/bin/rhwp

# 의존성 설치 및 캐시 정리
COPY --from=build-stage /tmp/requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir uv
RUN uv venv
RUN uv pip install --no-cache -r ./requirements.txt && \
    rm -rf /root/.cache/pip

# 임베딩용 PyTorch (CPU 전용 휠)
# 임베딩 모델은 app/utils/embedder.py를 통해 이 컨테이너 안에서 직접 실행한다.
# PyPI 휠은 nvidia-* CUDA 패키지를 함께 끌어와 이미지가 크게 불어나므로 PyTorch CPU
# 인덱스에서 받는다. GPU로 옮길 때는 이 줄의 인덱스만 cu###으로 바꾸면 된다.
RUN uv pip install --no-cache --index-url https://download.pytorch.org/whl/cpu torch==2.13.0 && \
    rm -rf /root/.cache/pip

# 난독화된 또는 일반 애플리케이션 및 PyArmor 런타임 복사
ARG ENABLE_OBFUSCATION=false
COPY --from=builder-stage /build/obfuscated/ ./

# 프로젝트 메타데이터 복사 및 실행 스크립트 복사
COPY pyproject.toml uv.lock* ./
COPY scripts ./scripts

# TERM 환경 변수 설정
ENV TERM=xterm-256color \
    PATH="/app/.venv/bin:${PATH}"

RUN install -m 0444 -o root -g root /dev/null /app/.jdone_guard

ENTRYPOINT ["sh", "./scripts/launch.sh"]
