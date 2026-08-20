#!/usr/bin/env bash
# 실측 평가 파일을 컨테이너 바인드 마운트(resources/)로 복사합니다.
# 채점 로직을 두 벌로 갈라 두지 않기 위해 eval/의 원본을 그대로 복사합니다.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST=resources/eval_live
mkdir -p "$DEST/results"
cp eval/live_eval.py          "$DEST/live_eval.py"
cp eval/reingest.py           "$DEST/reingest.py"
cp eval/live_scoring_shim.py  "$DEST/eval_scoring.py"
cp eval/scoring.py            "$DEST/scoring.py"
cp eval/stats.py              "$DEST/stats.py"
cp eval/questions_student.jsonl "$DEST/questions_student.jsonl"
echo "staged -> $DEST ($(ls "$DEST" | tr '\n' ' '))"
