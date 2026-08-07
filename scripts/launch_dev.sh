#!/usr/bin/env bash

# 환경 변수가 제공되지 않은 경우 기본값 설정
: "${HOST:=0.0.0.0}"
: "${PORT:=3000}"
: "${LOG_LEVEL:=debug}"
: "${LOG_CONFIG:=./configs/gunicorn_log_conf.ini}"

# .env 파일이 존재하는 경우 환경 변수 불러오기
if [ -f "./configs/.env" ]; then
    set -o allexport
    . ./configs/.env
    set +o allexport
fi

clear

# gunicorn 실행 (with live-reload)
uv run --active -m gunicorn \
    --reload \
    --worker-class app.worker.JdoneServerWorker \
    --bind "$HOST:$PORT" \
    --log-config "$LOG_CONFIG" \
    --timeout 600 \
    app.main:app
