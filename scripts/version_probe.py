#!/usr/bin/env python3
"""대상 호스트의 배포 상태를 한 덩어리 JSON으로 뽑는다.

개발(ys)과 운영(kmu-ai-gpu-01) 양쪽에서 **같은 스크립트**를 돌려 같은 모양의 사실을 만든다.
그 두 결과를 `version_log.py`가 비교해 "다릅니다, 배포하셔야 합니다"를 판정한다.

    python3 scripts/version_probe.py --env prod --out /tmp/vp.json [--with-db]

## 제약 (지키지 않으면 운영에서 못 돈다)

- **표준 라이브러리만.** 운영 서버는 인터넷이 없어 pip 설치를 할 수 없다.
- **Python 3.9 호환.** 운영은 3.9.19다. `match`, `X | Y`, `tomllib` 금지.
- **`app/` 를 import 하지 않는다.** 운영은 의존성이 컨테이너 안에만 있다.
- **출력은 ASCII.** HIWARE 게이트웨이를 지나며 plink가 비ASCII를 CP949로 훼손한다.
- **`probe_ok` 를 맨 마지막에 쓴다.** HIWARE는 세션이 조용해지면 종료코드 0에 빈 출력으로
  끊어버린다. 중간에 잘리면 이 키가 없으므로, 받는 쪽이 성공/실패를 구분할 수 있다.

## 공개 리포에 안전한 값만 남긴다

이 결과는 공개 저장소에 커밋된다. 설정값은 화이트리스트에 있는 키만, 그것도 세 등급으로만
기록한다. (`REDACTION` 참고)

- `plain`    값 그대로. `config.template.yaml`·`docker-compose.yml` 에 이미 공개된 것만.
- `shape`    분류 토큰만. 호스트·URL이 대상. `container:kmu-ai-redis` 처럼 남는다.
- `presence` `set`/`unset` 만. 비밀번호·시크릿·API 키.

**저엔트로피 값은 해시도 남기지 않는다.** `192.168.0.183` 의 SHA-256은 IPv4 전수(2^32)로
수 초 만에 역산되고, 비밀번호 해시는 공개 리포에 놓인 오프라인 크래킹 표적이 된다.
그래서 호스트는 해시가 아니라 `shape` 다. 반면 **파일 전체 해시**는 원본이 크고 추측
불가라 안전하며, "뭔가 바뀌었다"는 굵은 신호로 쓴다.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime

SCHEMA = 1

# 이 값들만 기록한다. 목록에 없는 설정 키는 아예 읽지 않는다.
REDACTION = {
    # 드리프트가 실제로 문제를 일으킨 것들 + 이미 공개된 값
    "chatbot.text_model": "plain",
    "chatbot.embedding_model": "plain",
    "chatbot.embedding_dim": "plain",
    "chatbot.top_k": "plain",
    "chatbot.score_threshold": "plain",
    "chatbot.history_limit": "plain",
    "chatbot.evidence_max_chars": "plain",
    "chatbot.source_content_max_chars": "plain",
    "chatbot.history_max_chars": "plain",
    "chatbot.query_max_chars": "plain",
    "chatbot.summary_trigger_count": "plain",
    "chatbot.ingestion_job_timeout_minutes": "plain",
    "milvus.enabled": "plain",
    "database.db_type": "plain",
    "database.name": "plain",
    "database.pool_max_connections": "plain",
    "s3.bucket_name": "plain",
    "s3.secure": "plain",
    # 호스트·주소는 형태만. 값이 새면 안 되지만 진단력은 필요하다.
    # (운영 장애 원인이 redis.host 가 엉뚱한 컨테이너를 가리킨 것이었다)
    "database.host": "shape",
    "redis.host": "shape",
    "memgraph.host": "shape",
    "milvus.host": "shape",
    "s3.endpoint": "shape",
    "auth.servers[0].server_url": "shape",
    "auth.servers[0].realm_name": "plain",
    "auth.servers[0].client_id": "plain",
    # 있는지 없는지만
    "auth.servers[0].client_secret_key": "presence",
    "auth.servers[0].admin_password": "presence",
    "database.password": "presence",
    "s3.access_key": "presence",
    "s3.secret_key": "presence",
}

# configs/.env 와 compose 환경변수 중 환경마다 갈리는 것들
ENV_KEYS = [
    "APP_ENV", "LOG_LEVEL", "WORKERS", "EMBEDDING_TORCH_THREADS",
    "ENABLE_SESSION_IDLE_TIMEOUT", "SESSION_IDLE_TIMEOUT_MINUTES",
    "ENABLE_SWAGGER", "ENABLE_OPENAPI",
]

EXTRA_IMAGES = ["jdone/vllm:latest"]
"""compose 에 없지만 추적해야 하는 이미지. GPU 서빙 이미지는 운영에만 있다."""

WATCH_FILES = [
    "docker-compose.yml",
    "Dockerfile",
    "configs/config.yaml",
    "configs/.env",
]
"""서버에서 손으로 고쳤는지 **개별로** 봐야 하는 파일.

나머지 수정 파일은 개수만 센다. 운영은 Windows 에서 tar 로 옮기며 CRLF 가 되어
280개 파일이 내용은 같은데도 "수정됨"으로 잡히기 때문이다. 이걸 전부 비교하면
경고가 300건씩 떠서 사람이 곧 무시하게 된다.
"""


# ── 실행 도우미 ────────────────────────────────────────────────────────────
def run(cmd, cwd=None, timeout=60):
    """명령을 돌려 (성공여부, stdout) 을 돌려준다. 실패해도 예외를 내지 않는다."""
    try:
        p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
        return p.returncode == 0, p.stdout.decode("utf-8", "replace").strip()
    except Exception:
        return False, ""


def sha256_file(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fp:
            for block in iter(lambda: fp.read(65536), b""):
                h.update(block)
        return "sha256:" + h.hexdigest()
    except OSError:
        return None


# ── 최소 YAML 리더 ────────────────────────────────────────────────────────
def read_yaml_scalars(path):
    """`configs/config.yaml` 에서 스칼라 잎만 점 표기 경로로 뽑는다.

    PyYAML 을 쓸 수 없어서(폐쇄망 + 표준 라이브러리 제약) 직접 읽는다. 이 파일에 필요한
    만큼만 지원한다 — 들여쓰기 중첩, `- ` 리스트 항목, `|` 블록 스칼라 건너뛰기.
    화이트리스트에 있는 키만 쓰이므로 완벽할 필요는 없다.

    Returns:
        dict: {"chatbot.text_model": "kmu-chat-gpu", "auth.servers[0].realm_name": "kmu-ai", ...}
    """
    try:
        with open(path, encoding="utf-8") as fp:
            lines = fp.read().splitlines()
    except OSError:
        return {}

    out = {}
    stack = []          # [(indent, 여기까지의 경로 문자열), ...]
    list_idx = {}       # 리스트 대시의 들여쓰기 -> 현재 인덱스
    skip_until = None   # 블록 스칼라를 건너뛰는 중인 들여쓰기

    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())

        if skip_until is not None:
            if indent > skip_until:
                continue
            skip_until = None

        line = raw.strip()

        # 지금 들여쓰기 이상인 경로는 이미 벗어난 것
        while stack and stack[-1][0] >= indent:
            stack.pop()

        if line.startswith("- "):
            # 리스트 항목. `- alias: x` 의 alias 는 대시보다 2칸 깊은 키로 취급한다.
            # 인덱스는 (들여쓰기, 부모경로)로 센다. 들여쓰기만으로 세면 형제 블록끼리
            # 카운터를 공유해 auth.servers[0] 이 [3] 이 되는 식으로 어긋난다.
            parent = stack[-1][1] if stack else ""
            slot = (indent, parent)
            idx = list_idx.get(slot, -1) + 1
            list_idx[slot] = idx
            stack.append((indent, "%s[%d]" % (parent, idx)))
            line = line[2:].strip()
            indent += 2
            while stack and stack[-1][0] >= indent:
                stack.pop()

        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        # 주석 제거. 값 자리가 통째로 주석이면(`whitelist_domains: # 설명`) 빈 값,
        # 즉 아래에 블록이 이어지는 키다. 이걸 값으로 잡으면 하위 항목을 통째로 놓친다.
        if value.startswith("#"):
            value = ""
        elif value and not value.startswith(("'", '"')):
            value = value.split(" #", 1)[0].strip()

        parent = stack[-1][1] if stack else ""
        path = "%s.%s" % (parent, key) if parent else key

        if value in ("|", ">", "|-", ">-"):
            skip_until = indent
            continue
        if value == "":
            stack.append((indent, path))
            continue
        out[path] = value

    return out


def coerce(text):
    """YAML 스칼라를 파이썬 값으로. 실패하면 문자열 그대로."""
    if text is None:
        return None
    t = text.strip().strip('"').strip("'")
    low = t.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


# ── 값 등급 적용 ──────────────────────────────────────────────────────────
PRIVATE_RE = re.compile(r"^(10\.|127\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.)")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def shape_of(value, container_names):
    """호스트·URL 을 유출 없는 분류 토큰으로 바꾼다."""
    if value is None or value == "":
        return "empty"
    text = str(value)
    scheme = ""
    if "://" in text:
        scheme, _, rest = text.partition("://")
        scheme += "://"
    else:
        rest = text
    host = rest.split("/")[0].split(":")[0]

    if host in container_names:
        token = "container:" + host
    elif host in ("localhost", "127.0.0.1"):
        token = "localhost"
    elif IPV4_RE.match(host):
        token = "private-ip" if PRIVATE_RE.match(host) else "public-ip"
    elif "." in host:
        token = "public-host"
    else:
        token = "name"
    return scheme + token


def classify(path, value, container_names):
    mode = REDACTION.get(path)
    if mode is None:
        return None
    if mode == "presence":
        return {"c": "presence", "v": "set" if value not in (None, "") else "unset"}
    if mode == "shape":
        return {"c": "shape", "v": shape_of(value, container_names)}
    return {"c": "plain", "v": coerce(value) if isinstance(value, str) else value}


# ── 수집 ──────────────────────────────────────────────────────────────────
def collect_code(repo):
    ok, commit = run(["git", "rev-parse", "HEAD"], cwd=repo)
    if not ok:
        return {"error": "git 정보를 읽지 못했습니다 (.git 이 없을 수 있습니다)"}
    _, branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    _, porcelain = run(["git", "status", "--porcelain"], cwd=repo)

    dirty_files = {}
    modified = untracked = 0
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:].strip().strip('"')
        if status == "??":
            untracked += 1
            dirty_files[path] = "UNTRACKED"
            continue
        modified += 1
        blob_ok, blob = run(["git", "hash-object", path], cwd=repo)
        dirty_files[path] = blob if blob_ok else "UNREADABLE"

    return {
        "branch": branch,
        "commit": commit,
        "commit_short": commit[:7],
        "dirty": bool(dirty_files),
        # 파일 단위로 전부 비교하면 못 쓴다. 운영은 Windows 에서 tar 로 옮기며 줄끝이
        # CRLF 로 바뀌어 280개 파일이 통째로 "수정됨"으로 잡힌다. 내용은 같은데도.
        # 그래서 비교는 아래 두 가지로만 하고, 파일 목록은 조회용으로만 남긴다.
        "dirty_counts": {"modified": modified, "untracked": untracked},
        "dirty_watch": {p: dirty_files[p] for p in WATCH_FILES if p in dirty_files},
        "dirty_files": dirty_files,
    }


def compose_facts(repo):
    """compose 파일에서 컨테이너명과 이미지 참조를 뽑는다."""
    path = os.path.join(repo, "docker-compose.yml")
    try:
        with open(path, encoding="utf-8") as fp:
            text = fp.read()
    except OSError:
        return [], []
    names = re.findall(r"^\s*container_name:\s*(\S+)", text, re.M)
    images = re.findall(r"^\s*image:\s*(\S+)", text, re.M)
    images = [i for i in images if "${" not in i]
    return names, images


def collect_docker(container_names, image_refs):
    """컨테이너가 **실제로 돌리고 있는 이미지 ID** 를 남긴다.

    태그로는 버전을 알 수 없다 — compose 가 전부 `:latest` 를 쓰고, 운영 이미지는
    레지스트리를 거치지 않고 `docker save`/`load` 로 손으로 나른다. 그런데 save/load 는
    **이미지 ID를 보존**하므로, 같은 빌드면 ys 와 운영에서 ID가 같다. 폐쇄망을 건너
    동일성을 확인할 수 있는 유일한 식별자다.
    """
    containers = {}
    for name in container_names:
        ok, raw = run(["docker", "inspect", name], timeout=90)
        if not ok or not raw:
            containers[name] = {"state": "absent"}
            continue
        try:
            info = json.loads(raw)[0]
        except (ValueError, IndexError, KeyError):
            containers[name] = {"state": "unreadable"}
            continue
        labels = (info.get("Config") or {}).get("Labels") or {}
        containers[name] = {
            "state": (info.get("State") or {}).get("Status"),
            "image_ref": (info.get("Config") or {}).get("Image"),
            "image_id": info.get("Image"),
            "labels": {k: v for k, v in labels.items()
                       if k.startswith("org.opencontainers.image.")},
        }

    images = {}
    for ref in image_refs:
        ok, raw = run(["docker", "image", "inspect", ref], timeout=90)
        if not ok or not raw:
            images[ref] = {"state": "absent"}
            continue
        try:
            info = json.loads(raw)[0]
        except (ValueError, IndexError):
            images[ref] = {"state": "unreadable"}
            continue
        images[ref] = {"id": info.get("Id"), "created": info.get("Created")}
    return containers, images


def collect_config(repo, container_names):
    files = {}
    for rel in ("configs/config.yaml", "configs/.env", "docker-compose.yml", "Dockerfile"):
        digest = sha256_file(os.path.join(repo, rel))
        if digest:
            files[rel] = digest

    scalars = read_yaml_scalars(os.path.join(repo, "configs", "config.yaml"))
    keys = {}
    for path in REDACTION:
        if path in scalars:
            entry = classify(path, scalars[path], container_names)
            if entry:
                keys[path] = entry

    # .env 는 값이 아니라 키 단위로만 본다
    env_path = os.path.join(repo, "configs", ".env")
    try:
        with open(env_path, encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                if k in ENV_KEYS:
                    keys["env." + k] = {"c": "plain", "v": coerce(v.strip().strip('"'))}
    except OSError:
        pass

    return {"files": files, "keys": keys}


def collect_constants(repo):
    def grep1(rel, pattern):
        try:
            with open(os.path.join(repo, rel), encoding="utf-8") as fp:
                m = re.search(pattern, fp.read(), re.M)
                return m.group(1) if m else None
        except OSError:
            return None

    return {
        "pyproject.version": grep1("pyproject.toml", r'^version\s*=\s*"([^"]+)"'),
        "frontend.version": grep1("frontend/package.json", r'"version"\s*:\s*"([^"]+)"'),
        # Dockerfile ARG 와 파이썬 상수가 손으로 동기화되고 있어 어긋날 수 있다
        "RHWP_VERSION.dockerfile": grep1("Dockerfile", r'^ARG RHWP_VERSION=(\S+)'),
        "RHWP_VERSION.python": grep1("app/utils/local_doc_parse.py", r'^RHWP_VERSION\s*=\s*"([^"]+)"'),
        # 올리면 전량 재색인 대상이 되는 값들
        "INGEST_VERSION": grep1("app/utils/regulation_ingest.py", r'^INGEST_VERSION\s*=\s*"([^"]+)"'),
        "EMBEDDING_VERSION": grep1("app/utils/faq_service.py", r'^EMBEDDING_VERSION\s*=\s*"([^"]+)"'),
    }


def collect_db(pg_container="kmu-ai-postgres"):
    ok, user = run(["docker", "exec", pg_container, "printenv", "POSTGRES_USER"])
    if not ok or not user:
        return {"error": "postgres 컨테이너에 붙지 못했습니다"}
    ok, db = run(["docker", "exec", pg_container, "printenv", "POSTGRES_DB"])
    db = db if ok and db else "kmu_ai"

    def psql(sql):
        good, out = run(["docker", "exec", pg_container, "psql", "-U", user, "-d", db,
                         "-tAF", "|", "-c", sql], timeout=120)
        return out if good else ""

    counts = {}
    for table in ("documents", "faq_items", "faq_embeddings", "collections",
                  "models", "chat_sessions", "document_chunks_1024"):
        val = psql("select count(*) from %s" % table).strip()
        if val.isdigit():
            counts[table] = int(val)

    # api_key 는 절대 읽지 않는다. 컬럼을 명시한다.
    # 목록이 아니라 이름을 키로 한 사전으로 남긴다. 목록이면 통째로 하나의 값이 되어
    # "어떤 모델의 무엇이 다른지"를 못 짚고, expected_diff 핀도 전체를 적어야 한다.
    models = {}
    for row in psql("select name,provider,model_id,model_type,status "
                    "from models order by name").splitlines():
        parts = row.split("|")
        if len(parts) == 5:
            models[parts[0]] = {"provider": parts[1], "model_id": parts[2],
                                "model_type": parts[3], "status": parts[4]}

    # 컬럼 구성 지문. 마이그레이션 원장이 없어서 스키마 차이를 이걸로 잡는다.
    # (peewee 의 create_tables(safe=True) 는 빠진 테이블은 만들지만 빠진 컬럼은 안 만든다)
    cols = psql("select table_name||'.'||column_name||':'||data_type "
                "from information_schema.columns where table_schema='public' "
                "order by 1")
    fingerprint = "sha256:" + hashlib.sha256(cols.encode("utf-8")).hexdigest() if cols else None

    return {"counts": counts, "models": models, "schema_fingerprint": fingerprint}


def collect_runtime(api_container="kmu-ai-api"):
    ok, ip = run(["docker", "inspect", "-f",
                  "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", api_container])
    if not ok or not ip:
        return {"error": "api 컨테이너 IP를 찾지 못했습니다"}
    ok, body = run(["curl", "-s", "-m", "10", "http://%s:13000/health" % ip])
    if not ok or not body:
        return {"error": "health 응답 없음"}
    try:
        return json.loads(body)
    except ValueError:
        return {"raw": body[:200]}


# ── main ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="배포 상태 수집기")
    ap.add_argument("--env", required=True, choices=["dev", "prod"])
    ap.add_argument("--repo", default=None, help="리포 경로 (기본: 이 스크립트의 상위)")
    ap.add_argument("--out", default=None, help="파일로 저장 (기본: stdout)")
    ap.add_argument("--with-db", action="store_true", help="DB 카운트·모델·스키마 지문까지")
    ap.add_argument("--no-docker", action="store_true", help="docker 조회 생략")
    args = ap.parse_args()

    repo = args.repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    doc = {
        "schema": SCHEMA,
        "env": args.env,
        "captured_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "code": collect_code(repo),
        "constants": collect_constants(repo),
    }

    names, images = compose_facts(repo)
    doc["config"] = collect_config(repo, set(names))

    if not args.no_docker:
        containers, image_facts = collect_docker(names, images + EXTRA_IMAGES)
        doc["containers"] = containers
        doc["images"] = image_facts
        doc["runtime"] = collect_runtime()
        if args.with_db:
            doc["db"] = collect_db()

    # 맨 마지막에 쓴다. 중간에 잘리면 이 키가 없어 받는 쪽이 실패로 판단한다.
    doc["probe_ok"] = True

    text = json.dumps(doc, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.out:
        with open(args.out, "w", encoding="ascii") as fp:
            fp.write(text)
        sys.stderr.write("wrote %s (%d bytes)\n" % (args.out, len(text)))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
