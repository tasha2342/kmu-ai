#!/usr/bin/env bash

# 환경 변수가 제공되지 않은 경우 기본값 설정
: "${HOST:=0.0.0.0}"
: "${PORT:=3000}"
: "${LOG_LEVEL:=info}"
: "${LOG_CONFIG:=./configs/gunicorn_log_conf.ini}"

# .env 파일이 존재하는 경우 환경 변수 불러오기
if [ -f "./configs/.env" ]; then
    set -o allexport
    . ./configs/.env
    set +o allexport
fi

# 가상환경 활성화
if [ -d "./.venv" ]; then
    . ./.venv/bin/activate
else
    echo "[ERROR] .venv 디렉토리를 찾을 수 없습니다." >&2
    exit 1
fi

# CPU 코어 수만큼 워커 설정
WORKERS=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)

# 로고 출력
cat << "EOF"
      _  _____    ____   _   _  ______      _____                 
     | ||  __ \  / __ \ | \ | ||  ____|    |_   _|                
     | || |  | || |  | ||  \| || |__         | |   _ __    ___    
 _   | || |  | || |  | || . ` ||  __|        | |  | '_ \  / __|   
| |__| || |__| || |__| || |\  || |____      _| |_ | | | || (__  _ 
 \____/ |_____/  \____/ |_| \_||______|    |_____||_| |_| \___|(_)

EOF

# gunicorn 실행
gunicorn \
    --workers $WORKERS \
    --worker-class app.worker.JdoneServerWorker \
    --bind "$HOST:$PORT" \
    --log-config "$LOG_CONFIG" \
    --timeout 600 \
    --control-socket /tmp/gunicorn.ctl \
    app.main:app
