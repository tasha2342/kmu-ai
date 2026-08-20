#!/usr/bin/env bash
# kmu-ai-api 이미지를 버전 태그와 OCI 라벨을 붙여 빌드한다.
#
#   ./scripts/build.sh              현재 HEAD 로 빌드
#   ./scripts/build.sh --push-ready 폐쇄망 반입용 tar.gz 까지 만든다
#
# 태그 체계는 Jenkinsfile 과 같다: YYYYMMDD-<짧은sha>
#
# ★ Windows 에서 돌릴 때 주의: 작업 트리가 CRLF 면 ENTRYPOINT(scripts/launch.sh) 가
#   컨테이너에서 깨진다. 그래서 git 에서 LF 트리를 따로 뽑아 그걸 컨텍스트로 쓴다.
#   `git archive` 만으로는 부족하다 — core.autocrlf=true 면 체크아웃과 같은 변환을
#   적용해 CRLF 로 나온다. (2026-08-20 에 이걸로 운영을 크래시 루프에 빠뜨렸다)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

GIT_COMMIT="$(git rev-parse HEAD)"
SHORT="$(git rev-parse --short HEAD)"
KMU_AI_VERSION="$(date +%Y%m%d)-${SHORT}"
IMAGE="${KMU_AI_IMAGE:-jdone/kmu-ai-api}"
CTX="$(mktemp -d)"
trap 'rm -rf "$CTX"' EXIT

echo "버전   : ${KMU_AI_VERSION}"
echo "이미지 : ${IMAGE}:${KMU_AI_VERSION}"

git -c core.autocrlf=false -c core.eol=lf archive HEAD | tar -x -C "$CTX"

# 검증: CR 이 하나라도 있으면 중단한다. grep 으로 CR 을 찾는 방식은 셸 이스케이프에 따라
# 조용히 오답을 내므로 바이트를 직접 센다.
for f in scripts/launch.sh scripts/launch_dev.sh; do
  cr="$(tr -cd '\r' < "$CTX/$f" | wc -c | tr -d ' ')"
  if [ "$cr" != "0" ]; then
    echo "빌드 중단: $f 에 CR 이 ${cr}개 있습니다. 컨테이너에서 ENTRYPOINT 가 깨집니다." >&2
    exit 1
  fi
done
echo "줄끝   : LF 확인"

docker build --platform linux/amd64 \
  --provenance=false --sbom=false \
  --build-arg ENABLE_OBFUSCATION=false \
  --build-arg GIT_COMMIT="${GIT_COMMIT}" \
  --build-arg IMAGE_VERSION="${KMU_AI_VERSION}" \
  --label "org.opencontainers.image.revision=${GIT_COMMIT}" \
  --label "org.opencontainers.image.version=${KMU_AI_VERSION}" \
  --label "org.opencontainers.image.title=kmu-ai-api" \
  -t "${IMAGE}:${KMU_AI_VERSION}" \
  -t "${IMAGE}:latest" \
  "$CTX"

# 기동 확인. ENTRYPOINT 가 깨졌으면 여기서 드러난다.
# (설정 파일 오류는 볼륨 없이 단독 실행해서 나는 것이라 정상)
echo "기동   : 확인 중…"
if docker run --rm --entrypoint sh "${IMAGE}:${KMU_AI_VERSION}" -c 'sh ./scripts/launch.sh 2>&1 | head -3' \
     | grep -qE "not found|Syntax error"; then
  echo "빌드 중단: ENTRYPOINT 가 컨테이너에서 실행되지 않습니다." >&2
  exit 1
fi
echo "기동   : ENTRYPOINT 정상"

echo
echo "완료. 폐쇄망 반입:"
echo "  docker save ${IMAGE}:${KMU_AI_VERSION} | gzip -1 > kmu-ai-api-${KMU_AI_VERSION}.tar.gz"
echo "  적재 후 라벨 확인:"
echo "    docker image inspect ${IMAGE}:${KMU_AI_VERSION} \\"
echo "      --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}'"
echo "    → ${GIT_COMMIT} 와 같아야 한다"
