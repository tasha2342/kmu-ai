"""4-arm 진단 — 검색 손실과 생성 손실을 분리합니다.

## 왜 3개가 아니라 4개인가

흔히 쓰는 3-arm(검색 / end-to-end / 오라클)은 **선택 실패와 독해 실패를 구분하지
못합니다.** 오라클이 컨텍스트의 *내용*(정답 유무)과 *노이즈 수준*(청크 1개 vs 12개)을
동시에 바꾸기 때문입니다. `검색 성공 + E2E 오답 + 오라클 정답`인 문항을 봐도
"12개 중에서 정답 청크를 못 골랐다"인지 "정답 청크를 줘도 못 읽는데 노이즈가 없어서
운 좋게 맞았다"인지 알 수 없습니다. 처방이 정반대로 갈립니다.

그래서 오라클을 둘로 쪼갭니다.

| Arm | 컨텍스트 | 분리하는 것 |
| --- | --- | --- |
| `r`   | (없음 — 검색만) | 검색: recall_article@k, MRR, first_rank |
| `e2e` | 실제 top-k, 프로덕션 예산으로 조립 | 통합 시스템의 실제 성능 |
| `on`  | **골드 청크를 1위에 + 실제 top-(k-1) distractor** | *선택* — 근거는 확실히 있고 노이즈는 그대로 |
| `oc`  | 골드 청크만 | *독해* — 노이즈 제거 |

## 오라클 컨텍스트 조립

별도 chunk_id가 필요 없습니다. gold_articles의 (doc_id, article)로 바로 찾습니다.
부칙을 제외한 3,967개 키 중 청크 2개 이상으로 쪼개지는 것은 74개(1.9%)뿐이라
사실상 안정적 청크 ID입니다. 여러 청크로 쪼개진 조항은 문서 순서대로 전부 넣습니다.
"""

from __future__ import annotations

import unicodedata

from typing import Any, Callable, Iterable, Optional


ARM_NAMES = ("r", "e2e", "on", "oc")

# 생성 arm. 검색 arm(`r`)은 LLM을 부르지 않습니다.
GENERATION_ARMS = ("e2e", "on", "oc")


def _norm(text: Any) -> str:
    return unicodedata.normalize("NFC", str(text or "")).strip()


def gold_chunks(
    question: dict[str, Any], chunks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """gold_articles에 해당하는 청크를 인덱스 순서(=문서 순서)대로 돌려줍니다."""
    wanted = {
        (g.get("doc_id"), _norm(g.get("article")))
        for g in (question.get("gold_articles") or [])
    }
    if not wanted:
        return []
    return [c for c in chunks if (c.get("doc_id"), _norm(c.get("article"))) in wanted]


def build_oracle_context(
    question: dict[str, Any],
    chunks: list[dict[str, Any]],
    hits: Optional[list[dict[str, Any]]] = None,
    top_k: int = 12,
    noisy: bool = False,
) -> list[dict[str, Any]]:
    """오라클 컨텍스트를 만듭니다.

    Args:
        noisy: False면 골드 청크만(=`oc`). True면 골드 청크를 1위에 놓고
            실제 검색 결과에서 골드가 아닌 것을 distractor로 채웁니다(=`on`).
            `on`의 총 개수는 top_k로 맞춥니다 — E2E와 노이즈 양을 같게 두어야
            'E2E는 틀리고 ON은 맞다'가 순위/예산 문제라고 말할 수 있습니다.
    """
    gold = gold_chunks(question, chunks)
    if not gold:
        return []

    if not noisy:
        return gold

    gold_keys = {(c.get("doc_id"), _norm(c.get("article"))) for c in gold}
    distractors = [
        h for h in (hits or []) if (h.get("doc_id"), _norm(h.get("article"))) not in gold_keys
    ]
    room = max(0, top_k - len(gold))
    return gold + distractors[:room]


def classify(
    r_hit: Optional[bool],
    oc_correct: Optional[bool],
    on_correct: Optional[bool],
    e2e_correct: Optional[bool],
) -> str:
    """(R, OC, ON, E2E) 4중항 → 실패 분류. 위에서부터 먼저 맞는 것으로 판정합니다.

    기존 scoring.classify_failure()를 확장하지 않고 새로 만든 이유:
    그쪽은 `"<표>"`, `EMPTY_TABLE_WARNING`, `normalize=="unknown"` 같은
    날짜/표 레인 전용 휴리스틱으로 **추측**합니다. 이 함수는 추측하지 않고
    실제로 돌린 4개 arm의 **측정값**에서 유도합니다.
    """
    # 골드 청크를 통째로 줬는데도 틀렸다 → 골드가 틀렸거나 정답이 그 청크에 없다.
    # 다른 어떤 분류보다 먼저 걸러야 합니다. 이걸 retrieval_miss로 세면
    # 검색을 아무리 고쳐도 안 고쳐지는 문항을 검색 탓으로 돌리게 됩니다.
    if oc_correct is False:
        return "gold_defect"

    if e2e_correct:
        # 검색이 실패했는데 답은 맞았다 → 모델 사전지식이거나 채점기 누출.
        # 정확도 지표에서 반드시 격리해야 하는 범주입니다.
        return "lucky_pass" if r_hit is False else "ok"

    if r_hit is False:
        return "retrieval_miss"

    # 여기부터는 검색이 성공했거나(r_hit=True) 판정 불가한(None) 경우입니다.
    # 생성 arm을 아예 안 돌린 실행(--arms r)에서는 e2e_correct가 None이므로
    # "틀렸다"고 말할 근거가 없습니다. generation_miss로 세면 돌리지도 않은
    # 생성 단계를 실패로 집계하게 됩니다.
    if e2e_correct is None:
        return "not_evaluated"

    # 검색은 성공했고 E2E는 틀렸다. ON이 갈라 줍니다.
    if on_correct is False:
        # 골드를 1위에 꽂아 줘도 distractor에 흔들린다 → 리랭커·section_type 필터·근거 예산
        return "selection_miss"
    if on_correct is True:
        # ON은 맞는데 E2E는 틀리다 → 골드가 top-k엔 있으나 순위가 낮아 예산에서 잘렸다
        return "ranking_miss"

    return "generation_miss"


# 분류별 처방. 리포트에 그대로 싣습니다.
REMEDY = {
    "ok": "—",
    # 실측(live) 경로에서만 나오는 분류입니다. 오프라인 하네스는 인메모리 인덱스를 항상
    # 전량 적재하지만, 운영 DB는 인제스트가 밀리면 골드 문서가 아예 없을 수 있습니다.
    "not_indexed": "**인제스트 문제** — 운영 DB에 문서가 없음. 검색 튜닝으로 안 고쳐짐",
    "gold_defect": "골드 수정 또는 청크 분할 문제. 수동 확인 필수",
    "retrieval_miss": "검색 개선 (질의 확장·문서 라우팅·리랭커)",
    "selection_miss": "리랭커 / section_type 필터 / 근거 예산 축소",
    "ranking_miss": "top_k 상향 / 근거 예산 / 리랭커",
    "generation_miss": "프롬프트·청크 포맷",
    "lucky_pass": "정확도 지표에서 격리. 검색은 실패한 문항임",
    "not_evaluated": "생성 arm 미실행 (--arms r). 검색만 잰 실행",
}


def run_arms(
    question: dict[str, Any],
    retriever: Any,
    top_k: int = 12,
    arms: Iterable[str] = ARM_NAMES,
    generate_fn: Optional[Callable[[str, str], str]] = None,
    evidence_fn: Optional[Callable[[list[dict[str, Any]]], str]] = None,
    title_routing: bool = False,
) -> dict[str, Any]:
    """문항 하나에 대해 요청된 arm들을 실행합니다.

    Args:
        generate_fn: (question_text, evidence) -> prediction. None이면 생성 arm을 건너뜁니다.
        evidence_fn: 히트 목록 → 근거 블록 문자열. 프로덕션 예산을 쓰는 함수를 넘기세요.

    Returns:
        {hits, contexts, predictions} — 채점은 호출자가 합니다
        (judge를 붙일지 말지가 호출자 쪽 결정이라서).
    """
    arms = tuple(arms)
    query = question["question"]

    hits = retriever.retrieve(query, top_k=top_k, title_routing=title_routing)

    out: dict[str, Any] = {"hits": hits, "contexts": {}, "predictions": {}}

    if not generate_fn or not evidence_fn:
        return out

    gold = gold_chunks(question, retriever.chunks)

    for arm in arms:
        if arm not in GENERATION_ARMS:
            continue

        if arm == "e2e":
            context_chunks = hits
        elif arm == "on":
            context_chunks = build_oracle_context(
                question, retriever.chunks, hits=hits, top_k=top_k, noisy=True
            )
        else:  # oc
            context_chunks = build_oracle_context(question, retriever.chunks)

        # 기권 문항(NEG-*)에는 골드 청크가 없습니다. 오라클 arm은 성립하지 않으므로
        # 건너뜁니다 — 빈 컨텍스트로 돌리면 "근거 없음"이 arm의 성질이 아니라
        # 문항의 성질인데도 독해 실패로 잘못 집계됩니다.
        if arm in ("on", "oc") and not gold:
            continue

        evidence = evidence_fn(context_chunks)
        out["contexts"][arm] = [
            {"doc_id": c.get("doc_id"), "article": c.get("article")} for c in context_chunks
        ]
        out["predictions"][arm] = generate_fn(query, evidence)

    return out
