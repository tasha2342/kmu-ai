#!/usr/bin/env python3
"""개발(ys)과 운영(kmu-ai-gpu-01)의 배포 상태를 비교하고 기록한다.

    python3 scripts/version_log.py report            # 기록된 상태만 읽고 비교 (네트워크 안 씀)
    python3 scripts/version_log.py probe --env dev   # ys 에 붙어 실제 상태 수집 후 기록
    python3 scripts/version_log.py probe --env prod  # HIWARE 터널로 운영 수집 후 기록
    python3 scripts/version_log.py record --env dev --from /tmp/vp.json
    python3 scripts/version_log.py audit ops/versions/prod.json
    python3 scripts/version_log.py commit --env dev

## 기록 위치

    ops/versions/dev.json    ys 의 Claude 만 쓴다
    ops/versions/prod.json   중계 PC 의 Claude 만 쓴다

**파일을 환경별로 나눈 이유**는 git 충돌을 없애기 위해서다. 하나로 합치면 두 머신이
같은 파일을 써서 매번 부딪힌다. 서로 다른 파일만 건드리면 `git pull --rebase` 가 항상
깨끗하게 통과한다. 이력은 `git log -p ops/versions/prod.json` 이 담당하므로 파일 안에
히스토리를 쌓지 않는다.

## 의도된 차이

운영과 개발은 원래 다른 부분이 있다(운영만 로컬 GPU 모델, 운영만 규정 181건 등).
그걸 매번 경고하면 사람이 곧 무시하게 된다. `prod.json` 의 `expected_diff` 에 선언하면
조용해진다. 다만 `mode: "pin"` 은 **값까지 고정**해서, 실제 값이 달라지면 `STALE-RULE`
로 다시 시끄러워진다. 규칙이 새 드리프트를 가리지 않게 하는 장치다.
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSIONS_DIR = os.path.join(REPO, "ops", "versions")
LOCAL_ENV = os.path.join(REPO, "ops", "local.env")

# 이 접두사로 시작하는 경로만 비교한다. 나머지는 참고용 기록일 뿐이다.
COMPARE_PREFIXES = (
    "code.commit", "code.dirty_counts.", "code.dirty_watch.",
    "constants.", "config.files.", "config.keys.",
    "containers.", "images.", "db.",
)
# code.dirty_files 는 일부러 뺐다. 운영은 CRLF 전환으로 280개 파일이 내용은 같은데도
# 수정됨으로 잡혀, 비교하면 경고가 300건씩 뜬다. 개수(dirty_counts)와 중요 파일
# (dirty_watch)만 비교하고 전체 목록은 조회용으로만 둔다.
# 매번 달라지는 값. 비교하면 항상 드리프트로 뜬다.
VOLATILE_SUFFIXES = (".created", ".started_at")
# 같은 사실을 두 번 세는 경로. code.commit 접두사가 code.commit_short 에도 걸리고,
# containers.*.image_id 와 images.*.id 는 같은 sha 다. 한 번만 센다.
DUPLICATE_PATHS = ("code.commit_short",)

# 공개 리포에 절대 들어가면 안 되는 것. record 전에 이 검사를 통과해야 한다.
FORBIDDEN = [
    (r"\b192\.168\.\d", "사설 IP"),
    (r"\b203\.247\.\d", "운영 서버 IP"),
    (r"\b10\.\d+\.\d+\.\d+", "사설 IP"),
    (r"[A-Za-z0-9.-]+\.(co\.kr|ac\.kr)\b", "내부 도메인"),
    (r"(?i)passw0rd", "비밀번호"),
    (r"(?i)\"[^\"]*password[^\"]*\"\s*:\s*\"(?!set\"|unset\")", "비밀번호 값"),
    (r"(?i)\"[^\"]*secret[^\"]*\"\s*:\s*\"(?!set\"|unset\")", "시크릿 값"),
    (r"SHA256:[A-Za-z0-9+/]{20}", "SSH 호스트키"),
    (r"sk-[A-Za-z0-9]{20}", "API 키"),
    (r"AIza[A-Za-z0-9_-]{30}", "Google API 키"),
]

# 이 스크립트는 SessionStart 훅으로도 돌고, 중계 PC의 콘솔은 기본이 cp949다.
# 한글 메시지를 그대로 쓰면 UnicodeEncodeError 로 죽어 세션 시작이 지저분해진다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

C = {"red": "\033[31m", "yellow": "\033[33m", "green": "\033[32m",
     "dim": "\033[2m", "bold": "\033[1m", "off": "\033[0m"}
if not sys.stdout.isatty():
    C = dict.fromkeys(C, "")


def out(text=""):
    sys.stdout.write(text + "\n")


# ── 파일 ──────────────────────────────────────────────────────────────────
def state_path(env):
    return os.path.join(VERSIONS_DIR, "%s.json" % env)


def load_state(env):
    try:
        with open(state_path(env), encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return None


def load_local_env():
    """접속 정보. 자격증명이 들어 있어 커밋하지 않는다 (.git/info/exclude)."""
    values = {}
    try:
        with open(LOCAL_ENV, encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    values[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


# ── 비교 ──────────────────────────────────────────────────────────────────
def flatten(doc, prefix=""):
    """중첩 dict 를 점 표기 경로로 편다. 리스트는 통째로 하나의 잎으로 본다."""
    flat = {}
    for key, value in (doc or {}).items():
        path = "%s.%s" % (prefix, key) if prefix else key
        if isinstance(value, dict):
            flat.update(flatten(value, path))
        elif isinstance(value, list):
            flat[path] = json.dumps(value, sort_keys=True, ensure_ascii=True)
        else:
            flat[path] = value
    return flat


def comparable(path):
    if path in DUPLICATE_PATHS:
        return False
    if any(path.endswith(s) for s in VOLATILE_SUFFIXES):
        return False
    return any(path.startswith(p) for p in COMPARE_PREFIXES)


def match_rule(path, rules):
    """가장 구체적인(긴) 규칙을 고른다. 경로 접두사도 허용한다."""
    best = None
    for rule in rules:
        rpath = rule.get("path", "")
        if path == rpath or path.startswith(rpath + "."):
            if best is None or len(rpath) > len(best.get("path", "")):
                best = rule
    return best


def compare(dev, prod):
    """(결과목록, 요약) 을 돌려준다. 결과 항목은 (판정, 경로, dev값, prod값, 사유)."""
    rules = (prod or {}).get("expected_diff", []) or []
    fdev, fprod = flatten(dev), flatten(prod)
    paths = sorted(p for p in set(fdev) | set(fprod) if comparable(p))

    rows = []
    for path in paths:
        a, b = fdev.get(path, "(없음)"), fprod.get(path, "(없음)")
        if a == b:
            continue
        rule = match_rule(path, rules)
        if rule is None:
            rows.append(("DRIFT", path, a, b, ""))
            continue
        if rule.get("mode") == "ignore":
            rows.append(("EXPECTED", path, a, b, rule.get("reason", "")))
            continue
        # pin: 선언된 값과 실제가 여전히 같은지 본다.
        # 어느 쪽 값도 선언하지 않은 pin 은 사실상 ignore 라 새 드리프트를 가린다.
        # 조용히 넘기지 않고 값을 적으라고 알린다.
        if "dev" not in rule and "prod" not in rule:
            rows.append(("EXPECTED", path, a, b,
                         "%s (주의: pin 인데 값이 선언되지 않아 ignore 처럼 동작합니다. "
                         "dev/prod 값을 적으면 값이 바뀔 때 다시 잡힙니다)"
                         % rule.get("reason", "")))
            continue
        want_dev = rule.get("dev", a)
        want_prod = rule.get("prod", b)
        if _same(a, want_dev) and _same(b, want_prod):
            rows.append(("EXPECTED", path, a, b, rule.get("reason", "")))
        else:
            rows.append(("STALE-RULE", path, a, b,
                         "선언값과 실제가 달라졌습니다: " + rule.get("reason", "")))

    summary = {v: 0 for v in ("DRIFT", "EXPECTED", "STALE-RULE")}
    for verdict, _, _, _, _ in rows:
        summary[verdict] += 1
    return rows, summary


def check_assertions(env, doc):
    """환경마다 **반드시 이래야 하는** 값을 검사한다.

    두 환경을 비교하는 도구는 **둘 다 똑같이 틀렸을 때 침묵한다.** 실제로 운영이
    APP_ENV=development 로 돌고 있었는데 개발과 값이 같아 드리프트로 잡히지 않았다.
    비교가 아니라 절대 기준이 필요한 항목은 여기 선언한다.

    Returns:
        list: (경로, 기대값, 실제값, 사유) 위반 목록
    """
    flat = flatten(doc)
    bad = []
    for rule in (doc or {}).get("assertions", []) or []:
        path = rule.get("path", "")
        actual = flat.get(path, "(없음)")
        # not_equals: "이 값이면 안 된다". 값이 배포마다 바뀌어 equals 로 고정할 수 없는데
        # 특정 값으로 떨어지는 것만은 막아야 하는 항목에 쓴다.
        # (운영 이미지가 :latest 로 되돌아가면 어느 빌드가 도는지 다시 알 수 없어진다)
        if "not_equals" in rule:
            if _same(actual, rule["not_equals"]):
                bad.append((path, "%s 이(가) 아닐 것" % rule["not_equals"],
                            actual, rule.get("reason", "")))
            continue
        if not _same(actual, rule.get("equals")):
            bad.append((path, rule.get("equals"), actual, rule.get("reason", "")))
    return bad


def _same(actual, declared):
    if isinstance(declared, (dict, list)):
        declared = json.dumps(declared, sort_keys=True, ensure_ascii=True)
    return str(actual) == str(declared)


def age_hours(doc):
    stamp = (doc or {}).get("captured_at")
    if not stamp:
        return None
    try:
        from datetime import datetime
        then = datetime.fromisoformat(stamp)
        now = datetime.now(then.tzinfo)
        return (now - then).total_seconds() / 3600.0
    except Exception:
        return None


# ── 명령: report ──────────────────────────────────────────────────────────
def cmd_report(args):
    dev, prod = load_state("dev"), load_state("prod")
    if dev is None and prod is None:
        out("%s[version]%s 기록이 없습니다. `version_log.py probe --env dev` 로 시작하세요."
            % (C["dim"], C["off"]))
        return 0

    def line_for(env, doc):
        # captured_at 이 없으면 아직 한 번도 수집되지 않은 것이다. expected_diff 만 미리
        # 심어 둔 씨앗 파일이 이 경우인데, 지어낸 사실이 없으므로 비교 대상이 아니다.
        if doc is None or not doc.get("captured_at"):
            return "%-4s %s미수집%s — probe --env %s 를 돌리세요" % (
                env, C["yellow"], C["off"], env)
        code = doc.get("code", {})
        dirty = ""
        n = len(code.get("dirty_files") or {})
        if n:
            dirty = " %s+%d수정%s" % (C["yellow"], n, C["off"])
        hours = age_hours(doc)
        stamp = (doc.get("captured_at") or "")[5:16].replace("T", " ")
        old = " %s(%.0f시간 지남)%s" % (C["yellow"], hours, C["off"]) if hours and hours > 48 else ""
        return "%-4s %s%s  %s%s%s%s" % (env, code.get("commit_short", "???????"), dirty,
                                        C["dim"], stamp, C["off"], old)

    out("%s[version]%s %s" % (C["dim"], C["off"], line_for("dev", dev)))
    out("%s[version]%s %s" % (C["dim"], C["off"], line_for("prod", prod)))

    if not (dev or {}).get("captured_at") or not (prod or {}).get("captured_at"):
        out("%s[version]%s 한쪽이 미수집이라 비교할 수 없습니다." % (C["dim"], C["off"]))
        return 0

    rows, summary = compare(dev, prod)
    drift = [r for r in rows if r[0] == "DRIFT"]
    stale = [r for r in rows if r[0] == "STALE-RULE"]
    violations = [(e, v) for e, d in (("dev", dev), ("prod", prod))
                  for v in check_assertions(e, d)]

    for envname, (path, want, actual, reason) in violations:
        out("%s[version]%s %s위반%s %s.%s — %s 여야 하는데 %s"
            % (C["dim"], C["off"], C["red"], C["off"], envname, path,
               _short(want, 40), _short(actual, 40)))
        if reason and not args.brief:
            out("      %s%s%s" % (C["dim"], reason, C["off"]))

    if not drift and not stale and not violations:
        out("%s[version]%s %s일치%s — 선언된 차이 %d건은 억제됨"
            % (C["dim"], C["off"], C["green"], C["off"], summary["EXPECTED"]))
    else:
        if drift:
            names = ", ".join(r[1] for r in drift[:3])
            more = " 외 %d건" % (len(drift) - 3) if len(drift) > 3 else ""
            out("%s[version]%s %sDRIFT %d건%s: %s%s"
                % (C["dim"], C["off"], C["red"], len(drift), C["off"], names, more))
        if stale:
            out("%s[version]%s %sSTALE-RULE %d건%s — 선언된 차이가 실제와 어긋납니다"
                % (C["dim"], C["off"], C["yellow"], len(stale), C["off"]))

    if not args.brief:
        out()
        width = max([len(r[1]) for r in rows] + [10])
        for verdict, path, a, b, reason in rows:
            color = {"DRIFT": C["red"], "STALE-RULE": C["yellow"]}.get(verdict, C["dim"])
            out("  %s%-10s%s %-*s" % (color, verdict, C["off"], width, path))
            out("      dev  %s" % _short(a))
            out("      prod %s" % _short(b))
            if reason:
                out("      %s%s%s" % (C["dim"], reason, C["off"]))
        out()
        out("  DRIFT %d / STALE-RULE %d / 선언된 차이 %d"
            % (summary["DRIFT"], summary["STALE-RULE"], summary["EXPECTED"]))
    elif drift or stale:
        out("%s[version]%s 자세히: /version-check" % (C["dim"], C["off"]))

    if args.strict:
        if violations or stale:
            return 2
        if drift:
            return 1
    return 0


def _short(value, limit=100):
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


# ── 명령: audit ───────────────────────────────────────────────────────────
def audit_text(text):
    hits = []
    for pattern, label in FORBIDDEN:
        for m in re.finditer(pattern, text):
            hits.append((label, m.group(0)[:60]))
    return hits


def cmd_audit(args):
    try:
        with open(args.path, encoding="utf-8") as fp:
            text = fp.read()
    except OSError as e:
        out("읽지 못했습니다: %s" % e)
        return 2
    hits = audit_text(text)
    if not hits:
        out("%s통과%s — 공개 리포에 커밋해도 되는 내용입니다" % (C["green"], C["off"]))
        return 0
    out("%s거부%s — 공개 저장소에 들어가면 안 되는 값이 있습니다:" % (C["red"], C["off"]))
    for label, sample in hits[:20]:
        out("  %-12s %s" % (label, sample))
    return 1


# ── 명령: record ──────────────────────────────────────────────────────────
def cmd_record(args):
    try:
        with open(args.source, encoding="utf-8") as fp:
            raw = fp.read()
    except OSError as e:
        out("수집 결과를 읽지 못했습니다: %s" % e)
        return 2
    try:
        doc = json.loads(raw)
    except ValueError as e:
        out("JSON 이 아닙니다: %s" % e)
        return 2
    if not doc.get("probe_ok"):
        out("%s거부%s — probe_ok 가 없습니다. 수집이 중간에 끊긴 결과입니다."
            % (C["red"], C["off"]))
        return 2
    if doc.get("env") != args.env:
        out("환경이 다릅니다: 파일은 %s, 요청은 %s" % (doc.get("env"), args.env))
        return 2

    # 사람이 선언한 값은 보존한다. 수집기는 이들을 모른다.
    old = load_state(args.env) or {}
    for key in ("expected_diff", "assertions"):
        if old.get(key):
            doc[key] = old[key]

    text = json.dumps(doc, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    hits = audit_text(text)
    if hits:
        out("%s거부%s — 공개 저장소에 들어가면 안 되는 값이 있습니다:" % (C["red"], C["off"]))
        for label, sample in hits[:10]:
            out("  %-12s %s" % (label, sample))
        out("scripts/version_probe.py 의 REDACTION 을 고쳐야 합니다.")
        return 1

    os.makedirs(VERSIONS_DIR, exist_ok=True)
    with open(state_path(args.env), "w", encoding="utf-8", newline="\n") as fp:
        fp.write(text)
    out("%s 기록했습니다: %s" % (args.env, os.path.relpath(state_path(args.env), REPO)))
    return 0


# ── 명령: probe ───────────────────────────────────────────────────────────
def sh(cmd, timeout=180):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except Exception as e:
        return 1, str(e)


def probe_dev(cfg):
    """ys 에 붙어 수집한다. 점프 호스트는 OpenSSH 키 인증, ys 자신은 비밀번호.

    수집기를 매번 올린 뒤 실행한다. 대상 리포가 이 스크립트보다 옛 커밋일 수 있어서
    (수집기를 막 만든 지금이 정확히 그 경우다) 리포 안의 사본에 기대면 안 된다.
    """
    host = cfg["YS_HOST"]  # ops/local.env 에서만 온다. 기본값을 코드에 두면 공개 리포에 사설 IP가 박힌다
    user = cfg.get("YS_USER", "jdone")
    repo = cfg.get("YS_REPO", "~/repos/kmu-ai")
    target = "%s@%s" % (user, host)
    base = ["-proxycmd", "ssh -W %%host:%%port %s" % cfg.get("YS_PROXY", "main"),
            "-pw", cfg["YS_PASSWORD"], "-hostkey", cfg["YS_HOSTKEY"], "-batch"]

    local = os.path.join(REPO, "scripts", "version_probe.py")
    rc, stdout = sh(["pscp"] + base + [local, "%s:/tmp/version_probe.py" % target], timeout=300)
    if rc != 0:
        out("수집기 전송에 실패했습니다.\n%s" % stdout[:300])
        return ""

    remote = "python3 /tmp/version_probe.py --env dev --with-db --repo %s" % repo
    rc, stdout = sh(["plink"] + base + [target, remote], timeout=900)
    if not stdout.strip().startswith("{"):
        out("수집에 실패했습니다.\n%s" % stdout[:500])
        return ""
    return stdout


def probe_prod(cfg):
    """HIWARE 터널로 운영에서 수집한다.

    게이트웨이 제약이 많아 순서가 중요하다.
      - 인증 횟수를 아낀다. 3초 간격 폴링으로 계정이 차단된 전례가 있다 (폴링 45초).
      - `pscp` 에는 `-share` 를 절대 붙이지 않는다. 연결공유 + 파일전송은 세션을 죽인다.
      - 출력이 멎으면 게이트웨이가 종료코드 0에 빈 출력으로 끊는다. 그래서 서버에서
        detach 로 돌리고 결과 파일을 폴링한다.
      - 명령줄은 전부 ASCII. plink 가 비ASCII 를 CP949 로 훼손한다.
    """
    port = cfg.get("PROD_PORT", "13000")
    user = cfg.get("PROD_USER", "gpuai")
    base = ["-P", port, "-hostkey", cfg["PROD_HOSTKEY"], "-pw", cfg["PROD_PASSWORD"], "-batch"]
    target = "%s@127.0.0.1" % user

    def plink(command, timeout=180):
        return sh(["plink"] + base + [target, command], timeout=timeout)

    rc, _ = plink("echo READY")
    if rc != 0:
        out("HIWARE 터널에 붙지 못했습니다. SFTP/PuTTY 세션을 먼저 열어주세요.")
        return ""

    # 운영 리포는 구버전이라 수집기가 없을 수 있다. 항상 최신본을 올린다.
    local = os.path.join(REPO, "scripts", "version_probe.py")
    rc, _ = sh(["pscp"] + base + [local, "%s:/tmp/version_probe.py" % target], timeout=300)
    if rc != 0:
        out("수집기 전송에 실패했습니다.")
        return ""

    plink("rm -f /tmp/vp.json /tmp/vp.done")
    launch = ("nohup setsid bash -c 'python3 /tmp/version_probe.py --env prod --with-db "
              "--repo $HOME/repos/kmu-ai --out /tmp/vp.json >/tmp/vp.log 2>&1; "
              "echo RC=$? > /tmp/vp.done' >/dev/null 2>&1 </dev/null & echo LAUNCHED")
    rc, stdout = plink(launch)
    if "LAUNCHED" not in stdout:
        out("수집기 실행 요청이 실패했습니다 (세션이 끊겼을 수 있습니다).")
        return ""

    for attempt in range(8):
        time.sleep(45)
        rc, stdout = plink("if [ -f /tmp/vp.done ]; then base64 -w0 /tmp/vp.json; "
                           "else echo WAIT; fi")
        text = stdout.strip()
        if not text or text == "WAIT":
            out("  대기 중… (%d/8)" % (attempt + 1))
            continue
        try:
            return base64.b64decode(text).decode("utf-8")
        except Exception:
            out("  결과를 디코드하지 못했습니다. 재시도합니다.")
    out("시간 내에 결과를 받지 못했습니다.")
    return ""


def cmd_probe(args):
    cfg = load_local_env()
    need = ["YS_HOST", "YS_PASSWORD", "YS_HOSTKEY"] if args.env == "dev" else ["PROD_PASSWORD", "PROD_HOSTKEY"]
    missing = [k for k in need if k not in cfg]
    if missing:
        out("ops/local.env 에 %s 가 필요합니다. (커밋되지 않는 파일)" % ", ".join(missing))
        return 2

    out("%s 상태를 수집합니다…" % args.env)
    raw = probe_dev(cfg) if args.env == "dev" else probe_prod(cfg)
    if not raw:
        return 2

    tmp = os.path.join(REPO, "ops", ".probe-%s.json" % args.env)
    with open(tmp, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(raw)
    rc = cmd_record(argparse.Namespace(env=args.env, source=tmp))
    try:
        os.remove(tmp)
    except OSError:
        pass
    return rc


# ── 명령: commit ──────────────────────────────────────────────────────────
def cmd_commit(args):
    rel = os.path.relpath(state_path(args.env), REPO).replace("\\", "/")
    doc = load_state(args.env) or {}
    short = (doc.get("code") or {}).get("commit_short", "?")

    rc, _ = sh(["git", "-C", REPO, "diff", "--quiet", "--", rel])
    if rc == 0:
        out("바뀐 내용이 없습니다.")
        return 0

    dev, prod = load_state("dev"), load_state("prod")
    n = len(compare(dev, prod)[0]) if dev and prod else 0
    message = "chore(versions): %s %s 기록 (차이 %d건)" % (args.env, short, n)

    for cmd in (["git", "-C", REPO, "add", rel],
                ["git", "-C", REPO, "commit", "-m", message]):
        rc, stdout = sh(cmd)
        if rc != 0:
            out("실패: %s\n%s" % (" ".join(cmd), stdout))
            return 1
    rc, stdout = sh(["git", "-C", REPO, "pull", "--rebase"], timeout=300)
    if rc != 0:
        out("pull --rebase 실패. 손으로 정리해주세요.\n%s" % stdout)
        return 1
    rc, stdout = sh(["git", "-C", REPO, "push"], timeout=300)
    if rc != 0:
        out("push 실패:\n%s" % stdout)
        return 1
    out("커밋·푸시 완료: %s" % message)
    return 0


# ── main ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="배포 버전 기록·비교")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("report", help="기록된 상태를 비교 (네트워크 안 씀)")
    p.add_argument("--brief", action="store_true", help="6줄 이내 요약")
    p.add_argument("--offline", action="store_true", help="(호환용) report 는 원래 파일만 읽는다")
    p.add_argument("--strict", action="store_true", help="차이가 있으면 종료코드 1/2")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("probe", help="서버에 붙어 상태를 수집하고 기록")
    p.add_argument("--env", required=True, choices=["dev", "prod"])
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("record", help="수집 결과 파일을 기록")
    p.add_argument("--env", required=True, choices=["dev", "prod"])
    p.add_argument("--from", dest="source", required=True)
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("audit", help="공개 리포에 커밋해도 되는지 검사")
    p.add_argument("path")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("commit", help="기록을 커밋·푸시")
    p.add_argument("--env", required=True, choices=["dev", "prod"])
    p.set_defaults(func=cmd_commit)

    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
