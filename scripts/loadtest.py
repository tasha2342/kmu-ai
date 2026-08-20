#!/usr/bin/env python3
"""vLLM 서빙 부하 테스트. TTFT 와 처리량을 동시성별로 잰다.

    python3 scripts/loadtest.py                      # 동시성 1,2,4,8,16 스윕
    python3 scripts/loadtest.py --concurrency 8      # 한 지점만
    python3 scripts/loadtest.py --mode chatbot       # 챗봇 전 경로(검색 포함)
    python3 scripts/loadtest.py --out /tmp/lt.json   # 결과를 JSON 으로

**운영 서버(H200 ×2)에서 돌리는 것을 전제로 한다.** 개발 서버에는 GPU 가 없어
같은 수치가 나오지 않는다.

## 두 가지 모드

- `vllm`(기본) — vLLM 의 OpenAI 호환 엔드포인트를 직접 친다. 검색·프롬프트 조립이
  빠진 **모델 서빙 자체의 성능**이다. GPU 용량 판단은 이 수치로 한다.
- `chatbot` — `/v1/chatbot/message` 를 친다. 의도 분류 + 검색 + 응답 생성이 전부
  포함된 **사용자가 체감하는 성능**이다. LLM 호출이 한 턴에 두 번 이상 일어난다.

## 재는 것

| 지표 | 뜻 |
| --- | --- |
| TTFT | 요청부터 첫 토큰까지. 사용자가 "멈췄나?" 느끼는 구간 |
| TPOT | 토큰 간 평균 간격. 낮을수록 술술 나온다 |
| 요청당 출력 토큰/초 | 한 사용자가 체감하는 생성 속도 |
| 총 출력 토큰/초 | 서버 전체 처리량. 동시성을 올리면 이게 늘어야 한다 |

동시성을 올렸을 때 **TTFT 는 늘고 총 처리량은 늘다가 꺾이는** 지점이 한계다.

제약은 다른 운영 스크립트와 같다 — 표준 라이브러리만, Python 3.9 호환, ASCII 출력.
(폐쇄망이라 pip 설치를 못 하고, plink 가 비ASCII 를 훼손한다)
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

FOLLOW_UPS = [
    "그럼 기간은 얼마나 되나요?",
    "그거 어디에 신청해요?",
    "그중에 제일 빠른 건 뭐야?",
    "거기 연락처는?",
    "그때는 어떻게 해야 해?",
]
"""다중 턴(`--turns`) 2턴부터 쓰는 후속 질문.

전부 지시어("그럼", "그거", "그중", "거기", "그때")를 포함한다. 앞 대화 없이는
의미가 통하지 않는 발화라야 `condense_query` 가 실제로 도는 경로를 재게 된다.
자기완결적인 질문을 넣으면 프리필터가 걸러 버려 비교 자체가 성립하지 않는다.
"""

DEFAULT_PROMPTS = [
    "정관 시행세칙 제1조의 목적을 설명해줘.",
    "학칙에서 휴학과 복학 절차를 정리해줘.",
    "장학금 종류와 신청 자격을 알려줘.",
    "기숙사 입사와 퇴사 절차가 어떻게 되나요?",
    "졸업에 필요한 학점과 요건을 알려줘.",
    "수강신청 기간과 정정 절차를 설명해줘.",
    "학생 징계 절차와 종류를 정리해줘.",
    "교직원 보수 규칙의 주요 내용을 요약해줘.",
]
"""실제 사용자 질문과 성격이 비슷한 것들. 길이가 제각각이라 한쪽으로 치우치지 않는다."""


def run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        return p.returncode == 0, p.stdout.decode("utf-8", "replace").strip()
    except Exception:
        return False, ""


def container_ip(name):
    ok, ip = run(["docker", "inspect", "-f",
                  "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", name])
    return ip if ok and ip else None


def find_vllm():
    """vLLM 컨테이너와 포트를 찾는다. 노드가 59000 부터 할당한다."""
    ok, names = run(["docker", "ps", "--filter", "name=vllm", "--format", "{{.Names}}"])
    if not ok or not names:
        return None, None
    name = names.splitlines()[0]
    ip = container_ip(name)
    ok, raw = run(["docker", "inspect", name])
    port = 59000
    try:
        info = json.loads(raw)[0]
        for spec in (info.get("Config") or {}).get("ExposedPorts", {}):
            port = int(spec.split("/")[0])
            break
    except Exception:
        pass
    return ip, port


class Result(object):
    __slots__ = ("ok", "ttft", "total", "out_tokens", "error", "session_id", "turn")

    def __init__(self):
        self.ok = False
        self.ttft = None
        self.total = None
        self.out_tokens = 0
        self.error = None
        self.session_id = None
        """챗봇이 SSE `session` 이벤트로 돌려주는 세션 ID. 다중 턴에 이어 쓴다."""
        self.turn = 1
        """이 요청이 대화의 몇 번째 턴인가. 1턴에는 이력이 없어 재작성이 돌지 않는다."""


def stream_request(url, payload, headers, timeout=300):
    """SSE 를 흘려 받으며 첫 토큰 도착 시각과 토큰 수를 잰다."""
    res = Result()
    body = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    started = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        res.error = "HTTP %s %s" % (e.code, e.read()[:120].decode("utf-8", "replace"))
        return res
    except Exception as e:
        res.error = str(e)[:120]
        return res

    try:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("event:"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            # 챗봇은 본문 델타보다 먼저 session 이벤트로 세션/메시지 ID 를 보낸다.
            if res.session_id is None and isinstance(obj, dict) and obj.get("session_id"):
                res.session_id = obj["session_id"]
            # vLLM: choices[0].delta.content / 챗봇: {"content": "..."}
            piece = ""
            if "choices" in obj:
                delta = (obj["choices"][0] or {}).get("delta") or {}
                piece = delta.get("content") or ""
            elif "content" in obj:
                piece = obj.get("content") or ""
            if not piece:
                continue
            if res.ttft is None:
                res.ttft = time.time() - started
            res.out_tokens += 1
    except Exception as e:
        res.error = str(e)[:120]
    finally:
        try:
            resp.close()
        except Exception:
            pass

    res.total = time.time() - started
    res.ok = res.error is None and res.out_tokens > 0
    return res


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    k = int(round((len(s) - 1) * p / 100.0))
    return s[k]


def run_wave(make_request, concurrency, requests_per_worker, turns=1):
    """동시에 N개를 띄워 한 파동을 돌린다.

    `turns` 가 1보다 크면 각 워커가 **한 세션을 이어가며** 여러 턴을 주고받는다.
    1턴에는 대화 이력이 없어 `condense_query` 가 아예 돌지 않으므로, 재작성 경로를
    재려면 반드시 2턴 이상이 필요하다.
    """
    results = []
    lock = threading.Lock()

    def worker(idx):
        local = []
        for r in range(requests_per_worker):
            session = None
            for t in range(turns):
                res = make_request(idx * requests_per_worker + r, session, t + 1)
                res.turn = t + 1
                local.append(res)
                if res.session_id:
                    session = res.session_id
                elif turns > 1:
                    # 세션 ID 를 못 받으면 다음 턴은 새 대화가 되어 측정이 무의미해진다.
                    res.error = res.error or "session_id 미수신"
                    res.ok = False
                    break
        with lock:
            results.extend(local)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(concurrency)]
    wall_start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.time() - wall_start
    return results, wall


def summarize(results, wall, concurrency):
    good = [r for r in results if r.ok]
    fail = [r for r in results if not r.ok]
    if not good:
        return {"concurrency": concurrency, "requests": len(results),
                "ok": 0, "failed": len(fail),
                "error_sample": (fail[0].error if fail else None)}

    ttfts = [r.ttft for r in good if r.ttft is not None]
    per_req_tps = [r.out_tokens / r.total for r in good if r.total and r.total > 0]
    # 토큰 간 간격 = (전체 - 첫 토큰까지) / (토큰 수 - 1)
    tpots = [(r.total - r.ttft) / (r.out_tokens - 1)
             for r in good if r.ttft is not None and r.out_tokens > 1 and r.total]
    total_out = sum(r.out_tokens for r in good)

    return {
        "concurrency": concurrency,
        "requests": len(results),
        "ok": len(good),
        "failed": len(fail),
        "wall_seconds": round(wall, 2),
        "ttft_p50": round(pct(ttfts, 50), 3) if ttfts else None,
        "ttft_p90": round(pct(ttfts, 90), 3) if ttfts else None,
        "ttft_max": round(max(ttfts), 3) if ttfts else None,
        "tpot_ms_p50": round(pct(tpots, 50) * 1000, 1) if tpots else None,
        "per_req_tokens_per_s": round(statistics.mean(per_req_tps), 1) if per_req_tps else None,
        "total_tokens_per_s": round(total_out / wall, 1) if wall > 0 else None,
        "output_tokens": total_out,
    }


def main():
    ap = argparse.ArgumentParser(description="vLLM/챗봇 부하 테스트")
    ap.add_argument("--mode", choices=["vllm", "chatbot"], default="vllm")
    ap.add_argument("--concurrency", type=int, default=None,
                    help="한 지점만 잰다. 생략하면 1,2,4,8,16 스윕")
    ap.add_argument("--requests-per-worker", type=int, default=2)
    ap.add_argument("--max-tokens", type=int, default=256)
    ap.add_argument("--model", default="kmu-chat-gpu")
    ap.add_argument("--base-url", default=None, help="직접 지정 (기본: docker 로 자동 탐색)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--warmup", type=int, default=1, help="측정 전 버리는 요청 수")
    ap.add_argument("--turns", type=int, default=1,
                    help="한 세션에서 주고받을 턴 수 (chatbot 전용). "
                         "2 이상이어야 후속 질문 재작성 경로를 잰다")
    args = ap.parse_args()

    if args.mode == "vllm":
        if args.base_url:
            base = args.base_url
        else:
            ip, port = find_vllm()
            if not ip:
                sys.stderr.write("vLLM 컨테이너를 찾지 못했습니다. --base-url 로 지정하세요.\n")
                return 2
            base = "http://%s:%d" % (ip, port)
        url = base + "/v1/chat/completions"
        headers = {}

        def make_request(i, session_id=None, turn=1):
            prompt = DEFAULT_PROMPTS[i % len(DEFAULT_PROMPTS)]
            return stream_request(url, {
                "model": args.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": args.max_tokens,
                "temperature": 0.0,
                "stream": True,
            }, headers)
    else:
        ip = container_ip("kmu-ai-api")
        if not ip:
            sys.stderr.write("kmu-ai-api 컨테이너를 찾지 못했습니다.\n")
            return 2
        base = args.base_url or ("http://%s:13000" % ip)
        # 챗봇은 인증이 필요하다. run-models.py 의 토큰 발급을 재사용한다.
        import importlib.util
        path = os.path.join(os.path.expanduser("~"), "run-models.py")
        if not os.path.exists(path):
            sys.stderr.write("~/run-models.py 가 필요합니다 (토큰 발급).\n")
            return 2
        spec = importlib.util.spec_from_file_location("rm", path)
        rm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rm)
        token = rm.get_token()
        url = base + "/v1/chatbot/message"
        headers = {"Authorization": "Bearer " + token}

        def make_request(i, session_id=None, turn=1):
            if turn == 1:
                prompt = DEFAULT_PROMPTS[i % len(DEFAULT_PROMPTS)]
            else:
                # 2턴부터는 지시어가 든 후속 질문. 재작성 경로를 실제로 태우기 위함이다.
                prompt = FOLLOW_UPS[(i + turn) % len(FOLLOW_UPS)]
            payload = {"message": prompt, "stream": True}
            if session_id:
                payload["session_id"] = session_id
            return stream_request(url, payload, headers)

    sys.stdout.write("mode=%s url=%s max_tokens=%d\n" % (args.mode, url, args.max_tokens))

    if args.warmup:
        sys.stdout.write("warmup %d건... " % args.warmup)
        sys.stdout.flush()
        for i in range(args.warmup):
            make_request(i, None, 1)
        sys.stdout.write("done\n")

    levels = [args.concurrency] if args.concurrency else [1, 2, 4, 8, 16]
    rows = []
    for c in levels:
        sys.stdout.write("concurrency=%-3d ... " % c)
        sys.stdout.flush()
        results, wall = run_wave(make_request, c, args.requests_per_worker, args.turns)
        row = summarize(results, wall, c)
        # 턴별로도 쪼갠다. condense_query 는 2턴부터만 도는 경로라, 전체 평균에
        # 1턴을 섞으면 그 비용이 희석되어 개선이 보이지 않는다.
        if args.turns > 1:
            row["by_turn"] = {}
            for t in range(1, args.turns + 1):
                sub = [r for r in results if r.turn == t]
                if sub:
                    row["by_turn"][str(t)] = summarize(sub, wall, c)
        rows.append(row)
        if row.get("ok"):
            sys.stdout.write(
                "TTFT p50=%.2fs p90=%.2fs | req %.1f tok/s | total %.1f tok/s | fail %d\n"
                % (row["ttft_p50"], row["ttft_p90"], row["per_req_tokens_per_s"],
                   row["total_tokens_per_s"], row["failed"]))
            for t, sub in sorted((row.get("by_turn") or {}).items()):
                if sub.get("ok"):
                    sys.stdout.write("    %s턴: TTFT p50=%.2fs p90=%.2fs (n=%d)\n"
                                     % (t, sub["ttft_p50"], sub["ttft_p90"], sub["ok"]))
        else:
            sys.stdout.write("전부 실패: %s\n" % row.get("error_sample"))

    sys.stdout.write("\n%-6s %-10s %-10s %-12s %-14s %s\n"
                     % ("동시성", "TTFT p50", "TTFT p90", "TPOT(ms)", "요청당 tok/s", "총 tok/s"))
    for r in rows:
        if not r.get("ok"):
            sys.stdout.write("%-6d (실패)\n" % r["concurrency"])
            continue
        sys.stdout.write("%-6d %-10s %-10s %-12s %-14s %s\n" % (
            r["concurrency"], r["ttft_p50"], r["ttft_p90"],
            r["tpot_ms_p50"], r["per_req_tokens_per_s"], r["total_tokens_per_s"]))

    if args.out:
        with open(args.out, "w", encoding="ascii") as fp:
            json.dump({"mode": args.mode, "url": url, "max_tokens": args.max_tokens,
                       "turns": args.turns, "rows": rows}, fp,
                      ensure_ascii=True, indent=2)
        sys.stdout.write("\n%s 에 저장했습니다.\n" % args.out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
