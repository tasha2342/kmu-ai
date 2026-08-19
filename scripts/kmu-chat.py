#!/usr/bin/env python3
"""kmu-ai 챗봇 터미널 클라이언트.

서버에 직접 붙어 챗봇을 시험하기 위한 도구입니다. 계명대 GPU 서버는 폐쇄망이라
브라우저 접근(X11 포워딩)이 불안정해서, 터미널에서 바로 질문·응답을 확인하려고 만들었습니다.

    python3 kmu-chat.py

의존성은 표준 라이브러리뿐입니다. (폐쇄망 서버에는 pip 설치가 안 됩니다)
Python 3.9에서 동작해야 하므로 3.10+ 문법(`X | None` 등)을 쓰지 않습니다.

접속 주소는 docker로 컨테이너 IP를 찾아 씁니다. 환경변수로 덮어쓸 수 있습니다.
    KMU_AI_API=http://127.0.0.1:8003 KMU_AI_KEYCLOAK=http://127.0.0.1:8082 python3 kmu-chat.py
"""

import base64
import getpass
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REALM = os.environ.get("KMU_AI_REALM", "kmu-ai")
FRONTEND_CLIENT = os.environ.get("KMU_AI_CLIENT", "kmu-ai-frontend")
DEFAULT_USER = os.environ.get("KMU_AI_USER", "jdone")

# 서버(`is_allowed_upload`)가 받는 형식과 크기입니다. 여기서 먼저 걸러 왕복을 아낍니다.
ALLOWED_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif",
               ".pdf", ".docx", ".txt", ".hwp", ".hwpx", ".md")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# ── 색상 ──────────────────────────────────────────────────────────────────
# 색을 못 내는 터미널이나 파이프로 넘길 때는 전부 빈 문자열로 만들어 깨지지 않게 합니다.
if sys.stdout.isatty() and os.environ.get("TERM") not in (None, "", "dumb"):
    DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"
    CYAN, GREEN, YELLOW, RED, BLUE = (
        "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[34m")
else:
    DIM = BOLD = RESET = CYAN = GREEN = YELLOW = RED = BLUE = ""

RULE = "-" * 58


def out(text=""):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def ask_line(prompt):
    sys.stdout.write(prompt)
    sys.stdout.flush()
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.rstrip("\n")


# ── 서버 주소 ─────────────────────────────────────────────────────────────
def container_ip(name):
    """컨테이너 IP를 찾습니다.

    계명대 서버는 호스트 발행 포트(localhost:8003 등)가 열리지 않아
    (정적 바이너리 Docker + nf_tables의 loopback DNAT 문제) 컨테이너 IP로 직접 붙어야 합니다.
    """
    try:
        r = subprocess.run(
            ["docker", "inspect", "-f",
             "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        return r.stdout.decode("utf-8", "replace").strip() or None
    except Exception:
        return None


def resolve_endpoints():
    api = os.environ.get("KMU_AI_API")
    kc = os.environ.get("KMU_AI_KEYCLOAK")
    if not api:
        ip = container_ip("kmu-ai-api")
        api = "http://%s:13000" % ip if ip else None
    if not kc:
        ip = container_ip("kmu-ai-keycloak")
        kc = "http://%s:8080" % ip if ip else None
    if not api or not kc:
        out(RED + "kmu-ai-api / kmu-ai-keycloak 컨테이너를 찾지 못했습니다." + RESET)
        out("  docker ps 로 확인하거나 환경변수로 직접 지정하세요:")
        out("    KMU_AI_API=http://172.18.0.11:13000 \\")
        out("    KMU_AI_KEYCLOAK=http://172.18.0.9:8080 python3 kmu-chat.py")
        sys.exit(1)
    return api.rstrip("/"), kc.rstrip("/")


# ── HTTP ─────────────────────────────────────────────────────────────────
def http(url, data=None, headers=None, method=None, timeout=60, stream=False):
    """요청을 보냅니다. stream=True면 응답 객체를 그대로 돌려줍니다. (SSE용)"""
    body = None
    hdrs = dict(headers or {})
    if data is not None:
        if isinstance(data, dict):
            body = json.dumps(data).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        else:
            body = data
    req = urllib.request.Request(url, data=body, headers=hdrs,
                                 method=method or ("POST" if body is not None else "GET"))
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return e.code, (e if stream else e.read())
    except urllib.error.URLError as e:
        out(RED + "서버에 연결하지 못했습니다: %s" % e.reason + RESET)
        return 0, b""
    if stream:
        return resp.status, resp
    return resp.status, resp.read()


def post_form(url, fields, timeout=60):
    status, raw = http(url, data=urllib.parse.urlencode(fields).encode(),
                       headers={"Content-Type": "application/x-www-form-urlencoded"},
                       timeout=timeout)
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {}


def decode_jwt(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return None


def build_multipart(field_name, file_name, payload, mime):
    """multipart/form-data 본문을 만듭니다.

    requests를 쓸 수 없어(폐쇄망, 표준 라이브러리만) 직접 조립합니다.
    파일명은 UTF-8 그대로 넣습니다. FastAPI(python-multipart)가 이대로 읽습니다.
    """
    boundary = "----kmu-chat-" + base64.b16encode(os.urandom(12)).decode("ascii").lower()
    head = (
        "--%s\r\n"
        "Content-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
        "Content-Type: %s\r\n\r\n" % (boundary, field_name, file_name, mime)
    ).encode("utf-8")
    tail = ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    return "multipart/form-data; boundary=%s" % boundary, head + payload + tail


def human_size(n):
    if n >= 1024 * 1024:
        return "%.1fMB" % (n / 1024.0 / 1024.0)
    if n >= 1024:
        return "%.0fKB" % (n / 1024.0)
    return "%dB" % n


# ── 인증 ─────────────────────────────────────────────────────────────────
class Auth(object):
    """Keycloak 토큰을 들고 있다가 만료 전에 갱신합니다.

    액세스 토큰 수명이 짧아(기본 5분) 대화 도중 만료됩니다. refresh_token으로
    조용히 갱신하고, 그것마저 만료되면 다시 로그인받습니다.
    """

    def __init__(self, keycloak):
        self.kc = keycloak
        self.access = None
        self.refresh = None
        self.exp = 0
        self.username = None
        self.roles = []

    def _token_url(self):
        return "%s/realms/%s/protocol/openid-connect/token" % (self.kc, REALM)

    def _apply(self, data):
        self.access = data.get("access_token")
        self.refresh = data.get("refresh_token")
        self.exp = time.time() + int(data.get("expires_in", 300))
        claims = decode_jwt(self.access) or {}
        self.username = claims.get("preferred_username") or self.username
        self.roles = (claims.get("realm_access") or {}).get("roles") or []

    def login(self, username=None, password=None):
        if username is None:
            typed = ask_line("계정 [%s]: " % DEFAULT_USER).strip()
            username = typed or DEFAULT_USER
        if password is None:
            password = getpass.getpass("비밀번호: ")
        status, data = post_form(self._token_url(), {
            "grant_type": "password",
            "client_id": FRONTEND_CLIENT,
            "scope": "openid profile email",
            "username": username,
            "password": password,
        })
        if status != 200 or not data.get("access_token"):
            reason = data.get("error_description") or data.get("error") or ("HTTP %s" % status)
            return False, reason
        self.username = username
        self._apply(data)
        return True, None

    def token(self):
        """유효한 액세스 토큰을 돌려줍니다. (만료 30초 전이면 미리 갱신)"""
        if self.access and time.time() < self.exp - 30:
            return self.access
        if self.refresh:
            status, data = post_form(self._token_url(), {
                "grant_type": "refresh_token",
                "client_id": FRONTEND_CLIENT,
                "refresh_token": self.refresh,
            })
            if status == 200 and data.get("access_token"):
                self._apply(data)
                return self.access
        out(YELLOW + "  세션이 만료되었습니다. 다시 로그인해주세요." + RESET)
        ok, err = self.login(username=self.username)
        if not ok:
            out(RED + "  로그인 실패: %s" % err + RESET)
            return None
        return self.access


# ── 챗봇 ─────────────────────────────────────────────────────────────────
class Chat(object):
    def __init__(self, api, auth):
        self.api = api
        self.auth = auth
        self.session_id = None
        self.last_sources = []
        # 업로드해 두고 다음 질문에 함께 보낼 첨부입니다. 전송에 성공하면 비웁니다.
        self.pending = []

    def upload(self, path):
        """파일을 첨부로 업로드합니다. 성공하면 서버가 준 메타를 pending에 넣습니다."""
        path = os.path.expanduser(path.strip().strip('"').strip("'"))
        if not os.path.isfile(path):
            out("  " + RED + "파일이 없습니다: %s" % path + RESET)
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in ALLOWED_EXT:
            out("  " + RED + "지원하지 않는 형식입니다: %s" % (ext or "(확장자 없음)") + RESET)
            out("  " + DIM + "허용: " + " ".join(ALLOWED_EXT) + RESET)
            return
        size = os.path.getsize(path)
        if size == 0:
            out("  " + RED + "빈 파일입니다." + RESET)
            return
        if size > MAX_UPLOAD_BYTES:
            out("  " + RED + "10MB를 넘습니다: %s" % human_size(size) + RESET)
            return

        token = self.auth.token()
        if not token:
            return
        with open(path, "rb") as fp:
            payload = fp.read()
        name = os.path.basename(path)
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        content_type, body = build_multipart("file", name, payload, mime)

        out("  " + DIM + "업로드 중… %s (%s)" % (name, human_size(size)) + RESET)
        status, raw = http(self.api + "/v1/chatbot/attachment", data=body,
                           headers={"Content-Type": content_type,
                                    "Authorization": "Bearer " + token},
                           timeout=300)
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        if status != 200 or not data.get("attachment_id"):
            out("  " + RED + "업로드 실패 (HTTP %s): %s"
                % (status, data.get("message") or str(data)[:200]) + RESET)
            return
        self.pending.append(data)
        out("  %s첨부 추가됨%s  %s  %s%s / %s%s"
            % (GREEN, RESET, data.get("file_name"),
               DIM, data.get("kind"), data.get("file_type"), RESET))
        out("  " + DIM + "다음 질문에 함께 전송됩니다. (/files 목록, /drop 비우기)" + RESET)

    def show_pending(self):
        if not self.pending:
            out("  " + DIM + "(대기 중인 첨부 없음)" + RESET)
            return
        for i, a in enumerate(self.pending, 1):
            out("  %s%d.%s %s  %s%s / %s%s"
                % (BOLD, i, RESET, a.get("file_name"),
                   DIM, a.get("kind"), human_size(a.get("size_bytes") or 0), RESET))

    def models(self):
        token = self.auth.token()
        if not token:
            return None
        status, raw = http(self.api + "/v1/model/list",
                           headers={"Authorization": "Bearer " + token})
        if status != 200:
            return None
        try:
            body = json.loads(raw)
        except ValueError:
            return None
        items = body.get("items") if isinstance(body, dict) else body
        if isinstance(items, dict):
            items = items.get("items", [])
        return items or []

    def ask(self, message):
        """질문을 보내고 SSE 스트림을 화면에 흘립니다."""
        token = self.auth.token()
        if not token:
            return
        # 이번 질문의 근거만 남깁니다. 첨부 질문처럼 sources 이벤트가 없는 턴에서
        # 직전 근거가 그대로 다시 표시되는 것을 막습니다.
        self.last_sources = []
        payload = {"message": message, "stream": True}
        if self.session_id:
            payload["session_id"] = self.session_id
        if self.pending:
            payload["attachments"] = self.pending
            names = ", ".join(a.get("file_name") or "?" for a in self.pending)
            out("  %s첨부 %d건 동봉: %s%s" % (DIM, len(self.pending), names, RESET))

        started = time.time()
        status, resp = http(self.api + "/v1/chatbot/message", data=payload,
                            headers={"Authorization": "Bearer " + token},
                            timeout=600, stream=True)
        if status != 200:
            body = resp.read() if hasattr(resp, "read") else b""
            out(RED + "  요청 실패 (HTTP %s): %s"
                % (status, body.decode("utf-8", "replace")[:300]) + RESET)
            return

        # 서버가 요청을 받아들였으므로 첨부는 소비된 것으로 봅니다.
        # (남겨 두면 다음 질문에 같은 파일이 또 붙습니다)
        self.pending = []

        event = None
        printed_header = False
        got_text = False
        try:
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    event = None          # 이벤트 경계
                    continue
                if line.startswith(":"):  # keep-alive 주석
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip())
                except ValueError:
                    continue

                if event == "session":
                    self.session_id = data.get("session_id") or self.session_id
                    out("  %s의도 %s%s" % (DIM, data.get("detected_intent"), RESET))
                    if data.get("notice"):
                        out("  %s안내 %s%s" % (YELLOW, data["notice"], RESET))
                elif event == "sources":
                    self.last_sources = data.get("sources") or []
                elif event == "delta":
                    if not printed_header:
                        sys.stdout.write(BOLD + GREEN + "챗봇 > " + RESET)
                        printed_header = True
                    chunk = data.get("content") or ""
                    if chunk:
                        got_text = True
                        sys.stdout.write(chunk)
                        sys.stdout.flush()
                elif event == "error":
                    out("\n  %s오류 %s%s" % (RED, data.get("message"), RESET))
                elif event == "done":
                    if printed_header:
                        out("")
                    self.render_footer(data, started)
        except KeyboardInterrupt:
            out("\n  " + DIM + "(중단)" + RESET)
        finally:
            try:
                resp.close()
            except Exception:
                pass
        if not got_text:
            out("  " + DIM + "(빈 응답)" + RESET)

    def render_footer(self, done, started):
        count = len(self.last_sources)
        if count:
            out("  %s근거 %d건%s  %s(/sources 로 전체)%s"
                % (DIM, count, RESET, DIM, RESET))
            for i, s in enumerate(self.last_sources[:3], 1):
                out("    %s%d. %.2f  %s%s"
                    % (DIM, i, s.get("score") or 0.0,
                       (s.get("question") or "")[:56], RESET))
        if not done.get("is_answered"):
            out("  %s미응답 사유: %s%s" % (YELLOW, done.get("unanswered_reason"), RESET))
        ms = done.get("latency_ms")
        if ms is None:
            ms = int((time.time() - started) * 1000)
        out("  %s%.1fs%s" % (DIM, ms / 1000.0, RESET))


# ── 화면 ─────────────────────────────────────────────────────────────────
HELP_LINES = [
    "  명령",
    "    /help              이 도움말",
    "    /new               새 대화 세션 (이전 맥락을 버립니다)",
    "    /sources           직전 응답의 근거 전체",
    "    /file <경로>       파일·이미지 첨부 (다음 질문에 함께 전송)",
    "    /files             대기 중인 첨부 목록",
    "    /drop              대기 중인 첨부 비우기",
    "    /status            모델·세션 상태",
    "    /quit              종료 (Ctrl-D 도 됩니다)",
    "",
    "  첨부는 png jpg jpeg webp gif pdf docx txt hwp hwpx md, 파일당 10MB까지.",
    "  이미지는 멀티모달로, 문서는 로컬 파싱 텍스트로 모델에 전달됩니다.",
    "  첨부만 보내려면 질문 없이 그냥 엔터를 치면 됩니다.",
    "",
    "  그 밖의 입력은 모두 챗봇 질문으로 전송됩니다.",
]


def banner(api, auth, chat):
    out()
    out(BOLD + CYAN + "  kmu-ai  계명대 AI 챗봇 · 터미널 클라이언트" + RESET)
    out("  " + DIM + RULE + RESET)
    out("  서버   %s" % api)
    role = "admin" if "admin" in auth.roles else (",".join(auth.roles[:2]) or "-")
    out("  계정   %s (%s)" % (auth.username, role))
    items = chat.models()
    if items is None:
        out("  모델   " + YELLOW + "조회 실패 (권한 또는 서버 확인)" + RESET)
    elif not items:
        out("  모델   " + YELLOW + "등록된 모델 없음" + RESET)
    else:
        for m in items:
            color = GREEN if m.get("status") == "running" else YELLOW
            out("  모델   %-14s %s%-8s%s %s"
                % (m.get("name"), color, m.get("status"), RESET, m.get("model_id")))
    out("  " + DIM + RULE + RESET)
    out("  " + DIM + "/help 로 명령 목록. 그냥 입력하면 질문이 전송됩니다." + RESET)
    out()


def show_sources(chat):
    if not chat.last_sources:
        out("  " + DIM + "(근거 없음)" + RESET)
        return
    out()
    for i, s in enumerate(chat.last_sources, 1):
        out("  %s%2d.%s %s%.4f%s  %s"
            % (BOLD, i, RESET, BLUE, s.get("score") or 0.0, RESET,
               s.get("question") or "(제목 없음)"))
        meta = []
        if s.get("source_type"):
            meta.append(s["source_type"])
        if s.get("source_id"):
            meta.append("id=%s" % s["source_id"])
        if s.get("source_url"):
            meta.append(s["source_url"])
        if meta:
            out("      " + DIM + " / ".join(meta) + RESET)
    out()


def main():
    api, kc = resolve_endpoints()
    auth = Auth(kc)

    out()
    out(BOLD + "  kmu-ai 로그인" + RESET + DIM + "  (Keycloak realm: %s)" % REALM + RESET)
    logged_in = False
    for _ in range(3):
        try:
            ok, err = auth.login()
        except (EOFError, KeyboardInterrupt):
            out("\n중단했습니다.")
            return 1
        if ok:
            logged_in = True
            break
        out("  " + RED + "로그인 실패: %s" % err + RESET)
    if not logged_in:
        out("  " + RED + "3회 실패로 종료합니다." + RESET)
        return 1

    chat = Chat(api, auth)
    banner(api, auth, chat)

    while True:
        try:
            line = ask_line(BOLD + "나 > " + RESET).strip()
        except (EOFError, KeyboardInterrupt):
            out("\n안녕히 가세요.")
            return 0

        if not line:
            # 질문 없이 엔터: 대기 중인 첨부만 보냅니다. (이미지 한 장만 던져 보는 경우)
            if chat.pending:
                out()
                chat.ask("")
                out()
            continue

        # /file 은 인자가 붙으므로 소문자 변환 전에 먼저 처리합니다. (경로 대소문자 보존)
        if line.split(" ", 1)[0].lower() in ("/file", "/img", "/image", "/attach"):
            parts = line.split(" ", 1)
            if len(parts) < 2 or not parts[1].strip():
                out("  " + YELLOW + "경로를 함께 적어주세요. 예: /file ~/test.png" + RESET)
            else:
                chat.upload(parts[1])
            continue

        cmd = line.lower()
        if cmd == "/files":
            chat.show_pending()
            continue
        if cmd == "/drop":
            chat.pending = []
            out("  " + DIM + "대기 중인 첨부를 비웠습니다." + RESET)
            continue
        if cmd in ("/quit", "/exit", "/q"):
            out("안녕히 가세요.")
            return 0
        if cmd == "/help":
            for l in HELP_LINES:
                out(l)
            continue
        if cmd == "/new":
            chat.session_id = None
            chat.last_sources = []
            out("  " + DIM + "새 세션을 시작합니다." + RESET)
            continue
        if cmd == "/sources":
            show_sources(chat)
            continue
        if cmd == "/status":
            banner(api, auth, chat)
            out("  세션   %s" % (chat.session_id or "(아직 없음)"))
            out()
            continue
        if cmd.startswith("/"):
            out("  " + YELLOW + "모르는 명령입니다. /help" + RESET)
            continue

        out()
        chat.ask(line)
        out()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
