#!/bin/bash
#
# NFS Server Setup Script (Interactive)
# 이 스크립트는 지정한 디렉토리를 NFS로 공유합니다.
#
# 사용법: sudo ./nfs_server.sh
#

set -e

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 로그 함수
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_prompt() {
    echo -e "${BLUE}[입력]${NC} $1"
}

# Root 권한 확인
if [ "$EUID" -ne 0 ]; then
    log_error "이 스크립트는 root 권한으로 실행해야 합니다."
    echo "사용법: sudo $0"
    exit 1
fi

# 현재 네트워크 정보 표시
echo ""
echo "============================================"
echo "       NFS 서버 설정 스크립트 (Interactive)"
echo "============================================"
echo ""
log_info "현재 서버 IP 정보:"
ip -4 addr show | grep inet | grep -v 127.0.0.1 | awk '{print "  - " $2}'
echo ""

# 사용자 입력 받기
DEFAULT_SHARE_DIR="/home/jdone/data"
log_prompt "공유할 디렉토리 경로를 입력하세요"
read -e -p "  [기본값: ${DEFAULT_SHARE_DIR}]: " INPUT_SHARE_DIR
SHARE_DIR="${INPUT_SHARE_DIR:-$DEFAULT_SHARE_DIR}"

# 경로 확장 (~를 실제 경로로)
SHARE_DIR="${SHARE_DIR/#\~/$HOME}"

echo ""
DEFAULT_NETWORK="192.168.1.0/24"
log_prompt "허용할 IP 대역을 입력하세요 (CIDR 형식)"
echo "  예시: 192.168.1.0/24, 10.0.0.0/8, 특정IP: 192.168.1.100/32"
read -e -p "  [기본값: ${DEFAULT_NETWORK}]: " INPUT_NETWORK
ALLOWED_NETWORK="${INPUT_NETWORK:-$DEFAULT_NETWORK}"

# 추가 네트워크 대역 입력 받기
ADDITIONAL_NETWORKS=()
echo ""
log_prompt "추가로 허용할 IP 대역이 있나요? (없으면 엔터)"
while true; do
    read -e -p "  추가 IP 대역 (완료시 엔터): " EXTRA_NETWORK
    if [ -z "$EXTRA_NETWORK" ]; then
        break
    fi
    ADDITIONAL_NETWORKS+=("$EXTRA_NETWORK")
done

EXPORTS_FILE="/etc/exports"

echo ""
echo "============================================"
log_info "=== 설정 확인 ==="
echo "  공유 디렉토리: ${SHARE_DIR}"
echo "  허용 네트워크: ${ALLOWED_NETWORK}"
for net in "${ADDITIONAL_NETWORKS[@]}"; do
    echo "                 ${net}"
done
echo "============================================"
echo ""

read -p "위 설정으로 진행하시겠습니까? (y/N): " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    log_warn "설정이 취소되었습니다."
    exit 0
fi

echo ""
log_info "=== NFS 서버 설정 시작 ==="

# 1. 공유 디렉토리 확인 및 생성
if [ ! -d "$SHARE_DIR" ]; then
    log_warn "공유 디렉토리가 없습니다. 생성합니다..."
    mkdir -p "$SHARE_DIR"
    chown -R jdone:jdone "$SHARE_DIR"
    chmod 755 "$SHARE_DIR"
    log_info "디렉토리 생성 완료: ${SHARE_DIR}"
else
    log_info "공유 디렉토리 확인 완료"
fi

# 2. NFS 서버 패키지 설치
log_info "NFS 서버 패키지 설치 중..."
apt-get update -qq
apt-get install -y -qq nfs-kernel-server > /dev/null 2>&1
log_info "NFS 서버 설치 완료"

# 3. /etc/exports 설정
# 기본 네트워크 + 추가 네트워크 모두 포함
EXPORT_LINE="${SHARE_DIR} ${ALLOWED_NETWORK}(rw,sync,no_subtree_check,no_root_squash)"
for net in "${ADDITIONAL_NETWORKS[@]}"; do
    EXPORT_LINE="${EXPORT_LINE} ${net}(rw,sync,no_subtree_check,no_root_squash)"
done

# 이미 설정되어 있는지 확인
if grep -q "^${SHARE_DIR}" "$EXPORTS_FILE" 2>/dev/null; then
    log_warn "기존 설정이 있습니다. 업데이트합니다..."
    sed -i "\|^${SHARE_DIR}|d" "$EXPORTS_FILE"
fi

echo "$EXPORT_LINE" >> "$EXPORTS_FILE"
log_info "/etc/exports 설정 추가 완료"

# 4. 방화벽 설정 (UFW 사용 시)
if command -v ufw &> /dev/null; then
    if ufw status | grep -q "active"; then
        log_info "UFW 방화벽 규칙 추가 중..."
        ufw allow from "$ALLOWED_NETWORK" to any port nfs > /dev/null 2>&1 || true
        ufw allow from "$ALLOWED_NETWORK" to any port 111 > /dev/null 2>&1 || true
        for net in "${ADDITIONAL_NETWORKS[@]}"; do
            ufw allow from "$net" to any port nfs > /dev/null 2>&1 || true
            ufw allow from "$net" to any port 111 > /dev/null 2>&1 || true
        done
        log_info "방화벽 규칙 추가 완료"
    fi
fi

# 5. NFS 서비스 설정 적용 및 재시작
log_info "NFS 서비스 설정 적용 중..."
exportfs -ra
systemctl enable nfs-kernel-server
systemctl restart nfs-kernel-server
log_info "NFS 서비스 재시작 완료"

# 6. 상태 확인
log_info "=== NFS 서버 설정 완료 ==="
echo ""
echo "현재 공유 목록:"
exportfs -v
echo ""
log_info "클라이언트에서 다음 명령어로 마운트할 수 있습니다:"
echo ""
echo "  sudo mount $(hostname -I | awk '{print $1}'):${SHARE_DIR} /mnt/resources"
echo ""
echo "또는 nfs_client.sh 스크립트를 클라이언트에서 실행하세요:"
echo ""
echo "  sudo ./nfs_client.sh $(hostname -I | awk '{print $1}') ${SHARE_DIR}"
echo ""
