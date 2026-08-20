"""judge 판정 대상을 배치 파일로 떨어뜨립니다 (에이전트 채점용).

`rescore.py`는 `Judge`를 통해 Anthropic API를 직접 부릅니다. API 키가 없을 때
같은 판정을 Claude 서브에이전트로 대신 만들어 `eval/judge_cache/`에 채워 넣기 위한
전처리입니다. 캐시가 차면 `rescore.py`는 API를 한 번도 부르지 않고 끝납니다.

**판정 규칙은 여기서 새로 쓰지 않습니다.** `judge.SYSTEM_PROMPT`와 `judge.build_prompt()`를
그대로 가져다 씁니다. 규칙이 두 벌로 갈리면 캐시된 판정과 API 판정이 다른 기준이 됩니다.

캐시 키도 `judge.cache_key()`를 그대로 씁니다. 키가 어긋나면 rescore가 캐시를 통째로
못 찾고 API로 넘어갑니다.

    PYTHONPATH=. python3 -m eval.judge_prep \
        --results resources/eval_live/results/L2_prod_4arm.jsonl \
        --questions eval/questions_student.jsonl \
        --batch-size 15
"""

from __future__ import annotations

import argparse
import json

from pathlib import Path

from eval.judge import CACHE_DIR, MODEL_ID, RUBRIC_VERSION, SYSTEM_PROMPT, build_prompt, cache_key

# 배치 대상은 rescore가 실제로 judge를 부르는 대상과 정확히 같아야 합니다. 목록을 두 벌로
# 두면 한쪽만 arm을 추가했을 때 "판정은 했는데 반영되지 않는" 조용한 어긋남이 생깁니다.
from eval.rescore import ARM_FIELDS


def main() -> None:
    p = argparse.ArgumentParser(description="judge 판정 대상 배치 생성")
    # 여러 실행을 한 배치 묶음으로 판정합니다. 캐시 키가 (문항, 예측)이라 실행이 달라도
    # 같은 응답이면 한 번만 판정되고, k=5와 k=12가 같은 답을 낸 문항이 실제로 많습니다.
    p.add_argument("--results", required=True, nargs="+")
    p.add_argument("--questions", required=True)
    p.add_argument("--out-dir", default="eval/judge_batches")
    p.add_argument("--batch-size", type=int, default=15)
    args = p.parse_args()

    gold = {
        json.loads(l)["id"]: json.loads(l)
        for l in Path(args.questions).read_text(encoding="utf-8").splitlines()
        if l.strip()
    }
    rows = [
        json.loads(l)
        for path in args.results
        for l in Path(path).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]

    items: list[dict] = []
    seen: set[str] = set()
    skipped_cached = 0

    for r in rows:
        q = gold.get(r["id"])
        if not q:
            continue
        for field, pred_field in ARM_FIELDS.items():
            pred = r.get(pred_field)
            # rescore.py의 호출 조건과 한 글자도 다르면 안 됩니다.
            if pred is None or r.get(field) is None:
                continue
            if r[field]:
                continue

            key = cache_key(q, pred)
            if (CACHE_DIR / f"{key}.json").exists():
                skipped_cached += 1
                continue
            # 같은 (문항, 예측)이 두 arm에 걸리면 캐시 키가 같습니다. 한 번만 판정합니다.
            if key in seen:
                continue
            seen.add(key)

            items.append(
                {
                    "cache_key": key,
                    "id": q["id"],
                    "arm_field": field,
                    "answer_type": q.get("answer_type", "exact"),
                    "prompt": build_prompt(q, pred),
                }
            )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("batch_*.json"):
        old.unlink()

    batches = [items[i : i + args.batch_size] for i in range(0, len(items), args.batch_size)]
    for n, batch in enumerate(batches):
        (out_dir / f"batch_{n:02d}.json").write_text(
            json.dumps(batch, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    (out_dir / "system_prompt.txt").write_text(SYSTEM_PROMPT, encoding="utf-8")

    print(
        json.dumps(
            {
                "items": len(items),
                "batches": len(batches),
                "batch_size": args.batch_size,
                "skipped_already_cached": skipped_cached,
                "out_dir": str(out_dir),
                "rubric_version": RUBRIC_VERSION,
                "judge_model": MODEL_ID,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
