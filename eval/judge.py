"""LLM-judge — 자유형 답변 채점.

현재 채점기는 `date|number|percent|list|text|unknown` 정규식이 전부입니다.
48문항이 전부 스칼라 추출이라 통했지만, 학생 골드셋 220문항 중 약 55%
(`list_all`/`conditional`/`abstain`)는 정규식으로 채점할 수 없습니다.
"징계받으면 장학금 못 받나요?"의 정답은 "근신·유기정학은 한 학기, 무기정학은 두 학기
경과 후 수혜 가능"인데, 어떤 정규화기도 이걸 채점하지 못합니다.

## 설계 원칙

**결정적 채점이 코어, judge는 보조.** scoring.score_answer_v2()가 정규화기를 먼저 돌리고
judge는 (a) 문항이 judge=true이거나 (b) 정규화기가 틀렸다고 본 경우에만 부릅니다.
**정규화기가 맞다고 한 것을 judge가 뒤집지 않습니다.** judge가 죽어도 오늘과 같은
동작으로 degrade하고, 채점 근거는 감사 가능한 상태로 남습니다.

**생성 모델이 아닌 모델이 채점합니다.** 답을 만든 Gemma가 자기 답을 채점하면
자기일관성을 정확도로 착각하게 됩니다. 그래서 Claude를 씁니다.

**judge는 자기 지식이 아니라 gold_spans에 대해 채점합니다.** 이 제약이 judge가
제2의 RAG로 변질되는 것을 막습니다. 한국 대학 규정을 "아는" judge는 코퍼스에 없는
사실로 오답을 정답 처리할 수 있습니다.

## 재현성

Opus 5는 `temperature`/`top_p`/`top_k`를 400으로 거부하고 thinking이 기본 on입니다.
온도 고정으로는 재현성을 못 얻으므로 다음 4가지로 확보합니다.

1. **디스크 캐시** — 같은 (문항, 예측) 쌍은 API를 다시 타지 않습니다.
   재실행이 비트 단위로 동일하고 무료입니다.
2. **루브릭·모델 고정** — 둘 중 하나가 바뀌면 캐시 키가 바뀌어 설계상 무효화됩니다.
3. **구조화 출력** — JSON 스키마 강제. 자유 텍스트 파싱이 없습니다.
4. **감사 로그** — 모든 판정을 커밋합니다. API 키 없이도 근거를 재확인할 수 있습니다.

    python3 -m eval.judge --selftest          # API 없이 캐시·프롬프트 조립 확인
    python3 -m eval.judge --calibrate PAIRS   # κ 측정 (수동 라벨 40쌍)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata

from pathlib import Path
from typing import Any, Optional


EVAL_DIR = Path(__file__).resolve().parent
CACHE_DIR = EVAL_DIR / "judge_cache"
RESULTS_DIR = EVAL_DIR / "results"

# 이 둘이 바뀌면 캐시가 무효화됩니다. 판정 결과의 출처를 고정하는 값입니다.
RUBRIC_VERSION = "kmu-judge-v1"
MODEL_ID = "claude-opus-5"

# thinking이 기본 on이고 max_tokens가 thinking+응답을 함께 덮어야 합니다.
# 판정 JSON 자체는 짧지만 여유를 둡니다.
MAX_TOKENS = 8000

# κ가 이 값 미만이면 judge 수치를 신뢰하지 않습니다.
KAPPA_THRESHOLD = 0.8

VERDICTS = ("correct", "partial", "incorrect", "abstain_ok", "hallucination")

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": list(VERDICTS),
            "description": (
                "correct=근거에 비추어 정답, partial=일부만 맞음, incorrect=오답, "
                "abstain_ok=근거가 없어 모른다고 답한 것이 정답, "
                "hallucination=근거에 없는 사실을 지어냄"
            ),
        },
        "matched_facts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "예측이 실제로 담고 있는 required_facts 항목",
        },
        "missing_facts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "예측에서 빠진 required_facts 항목",
        },
        "cited_span": {
            "type": "string",
            "description": "판정 근거가 된 gold_spans 구절. 없으면 빈 문자열",
        },
        "reason": {
            "type": "string",
            "description": "판정 이유 한두 문장. 한국어로 작성",
        },
    },
    "required": ["verdict", "matched_facts", "missing_facts", "cited_span", "reason"],
    "additionalProperties": False,
}


SYSTEM_PROMPT = """\
당신은 대학 규정 QA 시스템의 답변을 채점합니다.

## 절대 규칙

**제시된 근거(gold_spans)에 대해서만 채점하십시오.** 한국 대학 규정에 관한 당신의
사전 지식으로 채점하면 안 됩니다. 근거에 없는 내용을 예측이 말했다면, 그것이
실제 세계에서 참이더라도 근거에 없으므로 hallucination입니다. 반대로 근거에 있는
내용을 예측이 다른 표현으로 말했다면 정답입니다.

이 제약이 있는 이유: 채점기가 스스로 아는 것으로 채점하기 시작하면, 검색이 실패했는데도
모델이 사전지식으로 맞힌 답을 "정답"으로 세게 되어 검색 품질 측정이 무의미해집니다.

## 판정 기준

- correct — 근거에 비추어 정답. 표현이 달라도 사실이 맞으면 정답입니다.
  숫자·날짜·기간은 값이 같아야 합니다.
- partial — 정답의 일부만 맞음. answer_type이 list_all인데 항목이 빠진 경우가 대표적입니다.
- incorrect — 오답이거나, 물어본 것에 답하지 않았습니다.
- abstain_ok — answer_type이 abstain인 문항에서 "규정에 없다/알 수 없다"고 답했습니다.
  이 경우에만 씁니다.
- hallucination — 근거에 없는 구체적 사실(숫자, 날짜, 조항 번호, 기관명)을 지어냈습니다.
  answer_type이 abstain인데 구체적인 답을 지어낸 경우가 대표적입니다.

## 주의

- 예측에 사고 과정(<thought> 등)이 섞여 있으면 최종 답변 부분만 보고 채점하십시오.
- 예측이 정답을 말하면서 불필요한 말을 덧붙인 것은 감점 사유가 아닙니다.
- 근거가 "총장이 따로 정한다"인데 예측이 구체적 수치를 말했다면 hallucination입니다.
"""


def normalize_text(text: str) -> str:
    """캐시 키용 정규화. 공백 차이로 캐시가 갈리지 않게 합니다."""
    t = unicodedata.normalize("NFC", text or "")
    return re.sub(r"\s+", " ", t).strip()


def cache_key(question: dict[str, Any], prediction: str) -> str:
    payload = "␟".join(
        [
            str(question.get("id")),
            RUBRIC_VERSION,
            MODEL_ID,
            normalize_text(prediction),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_prompt(question: dict[str, Any], prediction: str) -> str:
    """judge에게 줄 사용자 메시지."""
    parts = [
        f"## 질문\n{question.get('question', '')}",
        f"\n## answer_type\n{question.get('answer_type', 'exact')}",
    ]

    if question.get("answer_type") == "abstain":
        parts.append(
            "\n## 근거\n이 문항은 규정집에 근거가 없습니다"
            f" (사유: {question.get('unanswerable_reason')})."
            " 모른다고 답해야 정답이고, 구체적인 답을 지어냈으면 hallucination입니다."
        )
    else:
        spans = question.get("gold_spans") or []
        body = "\n".join(f"- {s}" for s in spans) if spans else "(근거 구절 없음)"
        parts.append(f"\n## 근거 (규정 원문)\n{body}")
        parts.append(f"\n## 기준 정답\n{question.get('answer', '')}")

        alts = question.get("acceptable_answers") or []
        if alts:
            parts.append("\n## 정답으로 인정되는 다른 표현\n" + ", ".join(alts))

        required = question.get("required_facts") or []
        if required:
            parts.append(
                "\n## 전부 있어야 하는 항목 (required_facts)\n"
                + "\n".join(f"- {f}" for f in required)
            )

        forbidden = question.get("forbidden_facts") or []
        if forbidden:
            parts.append(
                "\n## 있으면 오답인 내용 (forbidden_facts)\n"
                + "\n".join(f"- {f}" for f in forbidden)
            )

    parts.append(f"\n## 채점할 예측 답변\n{prediction or '(빈 응답)'}")
    return "\n".join(parts)


class Judge:
    """캐시가 붙은 Claude judge.

    Args:
        offline: True면 API를 부르지 않고 캐시에 없는 항목은 error 판정을 냅니다.
            캐시만으로 리포트를 재생성할 때 씁니다 (API 키 없이).
    """

    def __init__(self, experiment: str = "adhoc", offline: bool = False):
        self.experiment = experiment
        self.offline = offline
        self._client: Any = None
        self.stats = {"cached": 0, "fresh": 0, "error": 0}
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        self.audit_path = RESULTS_DIR / f"{experiment}_judgments.jsonl"

    # ── 캐시 ────────────────────────────────────────────────────────────────

    def _cache_path(self, key: str) -> Path:
        return CACHE_DIR / f"{key}.json"

    def _read_cache(self, key: str) -> Optional[dict[str, Any]]:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None  # 캐시가 깨졌으면 다시 부릅니다

    def _write_cache(self, key: str, verdict: dict[str, Any]) -> None:
        self._cache_path(key).write_text(
            json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    # ── API ─────────────────────────────────────────────────────────────────

    def _ensure_client(self) -> Any:
        if self._client is None:
            import anthropic  # 지연 import — 캐시만 쓸 때는 SDK가 없어도 됩니다

            self._client = anthropic.Anthropic()
        return self._client

    def _call_api(self, question: dict[str, Any], prediction: str) -> dict[str, Any]:
        import anthropic

        client = self._ensure_client()
        try:
            response = client.messages.create(
                model=MODEL_ID,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                # 구조화 출력. 자유 텍스트를 파싱하지 않습니다.
                output_config={"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
                messages=[{"role": "user", "content": build_prompt(question, prediction)}],
            )
        except anthropic.APIStatusError as exc:
            return {"verdict": None, "reason": f"api_error_{exc.status_code}: {exc.message}"}
        except anthropic.APIConnectionError as exc:
            return {"verdict": None, "reason": f"api_connection_error: {exc}"}

        if response.stop_reason == "refusal":
            return {"verdict": None, "reason": "refusal"}

        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"verdict": None, "reason": f"unparseable: {text[:200]}"}

    # ── 공개 API ────────────────────────────────────────────────────────────

    def __call__(self, question: dict[str, Any], prediction: str) -> dict[str, Any]:
        """scoring.score_answer_v2(judge_fn=...)에 그대로 넘길 수 있는 형태."""
        key = cache_key(question, prediction)

        verdict = self._read_cache(key)
        source = "cache"
        if verdict is None:
            if self.offline:
                verdict = {"verdict": None, "reason": "offline_cache_miss"}
                source = "offline_miss"
            else:
                verdict = self._call_api(question, prediction)
                source = "api"
                # 오류 판정은 캐시하지 않습니다 — 다음 실행에서 다시 시도해야 합니다.
                if verdict.get("verdict") in VERDICTS:
                    self._write_cache(key, verdict)

        if verdict.get("verdict") in VERDICTS:
            self.stats["cached" if source == "cache" else "fresh"] += 1
        else:
            self.stats["error"] += 1

        self._audit(question, prediction, verdict, key, source)
        return verdict

    def _audit(
        self,
        question: dict[str, Any],
        prediction: str,
        verdict: dict[str, Any],
        key: str,
        source: str,
    ) -> None:
        """모든 판정을 감사 로그에 남깁니다. API 키 없이 근거를 재확인하기 위한 것."""
        record = {
            "id": question.get("id"),
            "category": question.get("category"),
            "answer_type": question.get("answer_type"),
            "question": question.get("question"),
            "gold_answer": question.get("answer"),
            "gold_spans": question.get("gold_spans"),
            "prediction": (prediction or "")[:1000],
            "verdict": verdict.get("verdict"),
            "reason": verdict.get("reason"),
            "matched_facts": verdict.get("matched_facts"),
            "missing_facts": verdict.get("missing_facts"),
            "cited_span": verdict.get("cited_span"),
            "cache_key": key,
            "source": source,
            "rubric_version": RUBRIC_VERSION,
            "judge_model": MODEL_ID,
        }
        with self.audit_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False) + "\n")

    def summary(self) -> dict[str, Any]:
        return {
            "judge_model": MODEL_ID,
            "rubric_version": RUBRIC_VERSION,
            "judge_calls": dict(self.stats),
            "judgments_path": str(self.audit_path.name),
        }


# ---------------------------------------------------------------------------
# 보정 — Cohen's kappa
# ---------------------------------------------------------------------------


def cohens_kappa(labels_a: list[str], labels_b: list[str]) -> float:
    """두 채점자의 일치도. 우연 일치를 보정합니다.

    단순 일치율을 쓰지 않는 이유: 판정이 correct에 몰려 있으면(실제로 그렇습니다)
    아무렇게나 correct를 찍어도 일치율이 높게 나옵니다.
    """
    if len(labels_a) != len(labels_b) or not labels_a:
        raise ValueError("두 라벨 목록의 길이가 같아야 하고 비어 있으면 안 됩니다")

    n = len(labels_a)
    observed = sum(1 for x, y in zip(labels_a, labels_b) if x == y) / n

    categories = set(labels_a) | set(labels_b)
    expected = sum(
        (labels_a.count(c) / n) * (labels_b.count(c) / n) for c in categories
    )
    if expected >= 1.0:
        return 1.0
    return (observed - expected) / (1 - expected)


def calibrate(pairs_path: Path) -> dict[str, Any]:
    """수동 라벨과 judge 판정을 비교해 κ를 냅니다.

    입력 JSONL: {"id", "question"(골드 레코드), "prediction", "human_verdict"}
    """
    rows = [json.loads(l) for l in pairs_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    judge = Judge(experiment="calibration")

    human, machine, disagreements = [], [], []
    for row in rows:
        verdict = judge(row["question"], row["prediction"])
        v = verdict.get("verdict")
        if v not in VERDICTS:
            continue
        human.append(row["human_verdict"])
        machine.append(v)
        if row["human_verdict"] != v:
            disagreements.append(
                {
                    "id": row["question"].get("id"),
                    "human": row["human_verdict"],
                    "judge": v,
                    "judge_reason": verdict.get("reason"),
                }
            )

    kappa = cohens_kappa(human, machine) if human else 0.0
    return {
        "n": len(human),
        "kappa": round(kappa, 4),
        "threshold": KAPPA_THRESHOLD,
        "accepted": kappa >= KAPPA_THRESHOLD,
        "agreement_rate": round(
            sum(1 for a, b in zip(human, machine) if a == b) / len(human), 4
        )
        if human
        else 0.0,
        "disagreements": disagreements,
        **judge.summary(),
    }


def _selftest() -> int:
    """API 없이 캐시 키·프롬프트 조립·kappa를 확인합니다."""
    q = {
        "id": "JAN-002",
        "category": "JAN",
        "question": "징계받으면 장학금 못 받아?",
        "answer": "근신·유기정학은 한 학기, 무기정학은 두 학기 경과 후 수혜 가능",
        "answer_type": "conditional",
        "gold_spans": ["① 장학생의 자격에 관하여 ... 재학 중 징계가 없는 자로 한다."],
        "required_facts": ["근신·유기정학은 한 학기", "무기정학은 두 학기"],
    }

    k1 = cache_key(q, "근신은 한 학기 뒤부터요")
    k2 = cache_key(q, "근신은  한 학기   뒤부터요")  # 공백만 다름
    assert k1 == k2, "공백 차이로 캐시 키가 갈립니다"

    k3 = cache_key(q, "무기정학은 두 학기")
    assert k1 != k3, "예측이 다른데 캐시 키가 같습니다"

    prompt = build_prompt(q, "근신은 한 학기 뒤부터입니다")
    for needed in ("## 질문", "## 근거 (규정 원문)", "## 채점할 예측 답변", "required_facts"):
        assert needed in prompt, f"프롬프트에 {needed}가 없습니다"

    neg = {"id": "NEG-002", "question": "등록금 얼마?", "answer_type": "abstain",
           "unanswerable_reason": "not_in_corpus"}
    neg_prompt = build_prompt(neg, "약 400만원입니다")
    assert "hallucination" in neg_prompt, "기권 문항 프롬프트에 환각 지시가 없습니다"
    assert "기준 정답" not in neg_prompt, "기권 문항에 기준 정답이 들어갔습니다"

    # kappa: 완전 일치는 1.0, 우연 수준이면 0 근처
    assert abs(cohens_kappa(["correct"] * 5 + ["incorrect"] * 5,
                            ["correct"] * 5 + ["incorrect"] * 5) - 1.0) < 1e-9
    mixed = cohens_kappa(["correct", "correct", "incorrect", "incorrect"],
                         ["correct", "incorrect", "correct", "incorrect"])
    assert abs(mixed) < 1e-9, f"우연 일치인데 kappa={mixed}"

    print("selftest 통과: 캐시 키 정규화, 프롬프트 조립, 기권 분기, kappa")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="LLM-judge")
    p.add_argument("--selftest", action="store_true", help="API 없이 내부 로직 확인")
    p.add_argument("--calibrate", metavar="PAIRS_JSONL", help="수동 라벨과 비교해 κ 측정")
    p.add_argument("--cache-stats", action="store_true", help="캐시 현황")
    args = p.parse_args()

    if args.selftest:
        sys.exit(_selftest())
    if args.cache_stats:
        n = len(list(CACHE_DIR.glob("*.json"))) if CACHE_DIR.exists() else 0
        print(json.dumps({"cached_verdicts": n, "cache_dir": str(CACHE_DIR),
                          "rubric_version": RUBRIC_VERSION, "judge_model": MODEL_ID},
                         ensure_ascii=False, indent=2))
        return
    if args.calibrate:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("[경고] ANTHROPIC_API_KEY가 없습니다. 캐시에 있는 판정만 쓰입니다.", file=sys.stderr)
        result = calibrate(Path(args.calibrate))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["accepted"]:
            print(f"\n[중단] κ={result['kappa']} < {KAPPA_THRESHOLD}. "
                  "루브릭을 고치기 전에는 judge 수치를 신뢰하지 마세요.", file=sys.stderr)
            sys.exit(1)
        return

    p.print_help()


if __name__ == "__main__":
    main()
