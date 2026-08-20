"""컨테이너용 채점 shim — `eval/` 패키지를 그대로 재사용합니다.

`/app/resources`만 바인드 마운트되어 있어 `eval/` 패키지를 임포트할 수 없으므로,
필요한 모듈(`scoring.py`, `stats.py`)을 같은 디렉터리에 복사해 두고 여기서 묶습니다.
**채점 로직을 두 벌로 갈라 두지 않기 위해** 재구현하지 않고 복사본을 임포트합니다.
(복사는 `eval/stage_live.sh`가 합니다 — 손으로 옮기면 두 벌이 어긋납니다.)
"""

from __future__ import annotations

from typing import Any, Optional

from scoring import score_answer_v2, score_retrieval_article  # noqa: F401
from stats import fmt_ci  # noqa: F401


def classify_arms(
    r_hit: Optional[bool],
    oc_correct: Optional[bool],
    on_correct: Optional[bool],
    e2e_correct: Optional[bool],
    gold_docs_indexed: bool = True,
) -> str:
    """(R, OC, ON, E2E) → 실패 분류. 오프라인 판본에 `not_indexed`가 추가됩니다.

    **`not_indexed`가 가장 먼저 걸러져야 합니다.** 골드 문서가 운영 DB에 애초에
    색인되지 않았다면 검색이 못 찾는 게 당연하고, 이걸 `retrieval_miss`로 세면
    검색 알고리즘을 아무리 튜닝해도 안 고쳐지는 문항을 검색 탓으로 돌리게 됩니다.
    처방이 완전히 다릅니다 — 이건 인제스트를 고쳐야 하는 문제입니다.
    """
    if not gold_docs_indexed:
        return "not_indexed"

    if oc_correct is False:
        return "gold_defect"

    if e2e_correct:
        return "lucky_pass" if r_hit is False else "ok"

    if r_hit is False:
        return "retrieval_miss"

    if e2e_correct is None:
        return "not_evaluated"

    if on_correct is False:
        return "selection_miss"
    if on_correct is True:
        return "ranking_miss"
    return "generation_miss"


REMEDY = {
    "ok": "—",
    "not_indexed": "**인제스트 문제** — 운영 DB에 문서가 없음. 검색 튜닝으로 안 고쳐짐",
    "gold_defect": "골드 오류 또는 청크 분할. 수동 확인",
    "retrieval_miss": "검색 개선 (질의 확장·문서 라우팅·리랭커)",
    "selection_miss": "리랭커 / section_type 필터 / 근거 예산",
    "ranking_miss": "top_k 상향 / 근거 예산",
    "generation_miss": "프롬프트·청크 포맷",
    "lucky_pass": "정확도에서 격리. 검색은 실패한 문항",
    "not_evaluated": "생성 arm 미실행",
}
