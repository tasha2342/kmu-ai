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

# 워커 수. 환경 변수로 지정하지 않으면 CPU 코어 수를 쓴다.
#
# 상한을 정하는 것은 CPU가 아니라 메모리인 경우가 많다. 워커마다 임베딩 모델을 자기 메모리에
# 따로 올리므로(app/utils/embedder.py는 프로세스별 싱글턴), KURE-v1 기준 워커 하나당 약 2.2GB가
# 더 필요하다. 코어 수만 보고 워커를 띄우면 메모리가 모자라 스와핑이 일어나고, 짧은 문장 하나를
# 임베딩하는 데 수십~수백 초가 걸린다. 그래서 WORKERS로 덮어쓸 수 있게 둔다.
#
# sysctl -n hw.ncpu는 macOS 전용이라 리눅스에서는 항상 실패해 기본값으로 떨어졌었다.
if [ -z "$WORKERS" ]; then
    WORKERS=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
fi

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
