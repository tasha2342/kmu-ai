"""학생 말투 → 규정 어휘 동의어 확장. (KAI-REQ-013 검색 품질)

규정은 "생활관"이라고 쓰는데 학생은 "기숙사"라고 묻습니다. 어휘 검색은
`TEXT_SEARCH_CONFIG = "simple"`이라 형태소 분석도 동의어 처리도 없어서, 이 격차가
그대로 검색 실패가 됩니다. 2026-08-12 실측(`docs/kmu_ai_eval.md`)에서 top-k=12로도
못 찾은 문항 중 3건이 순위 문제가 아니라 **단어가 다른** 문제였습니다.

## 왜 치환이 아니라 OR인가

"기숙사"를 "생활관"으로 **바꾸면** 안 됩니다. 코퍼스에 "기숙사"가 실제로 9청크,
"교생"이 27청크 들어 있어서, 치환하면 그 청크들을 잃습니다. 원래 질의도 살리고
규정 어휘도 함께 보는 OR이라야 합니다.

## 왜 확장어를 질의에 덧붙이지 않는가

`plainto_tsquery`는 입력을 **AND**로 묶습니다. "기숙사 생활관 퇴사"로 붙이면
세 단어를 **모두** 가진 청크만 걸려서 어휘 검색이 오히려 죽습니다. 그래서
`vector_store._lexical_expressions()`가 `plainto_tsquery(원문) || plainto_tsquery(확장어)`
형태로 tsquery를 OR 결합합니다. 결과 집합이 원래의 상위집합이라 어휘 recall이
줄어들 수 없습니다.

## 사전에 넣는 기준

**확장 대상 단어가 코퍼스에 실제로 있어야 합니다.** 없는 단어로 확장하면 후보만
늘리고 아무것도 못 찾습니다. 아래 청크 수는 2026-08-12 코퍼스(181문서 5,359청크)
실측이며, 사전을 늘릴 때도 같은 방식으로 확인하고 주석에 남기세요.

    SELECT count(*) FROM document_chunks_1024
     WHERE collection_name='kmu_regulations' AND content LIKE '%생활관%';

너무 흔한 단어로 확장하지 마세요. 어휘 후보군을 채워 버려 정작 필요한 청크를
밀어냅니다. (예: "성적" 166청크는 확장어로 부적절합니다.)
"""

from __future__ import annotations

import re


# (질의에 이 중 하나가 있으면, 이 규정 어휘들을 OR로 함께 찾는다)
# 뒤 숫자는 2026-08-12 코퍼스 실측 청크 수입니다.
SYNONYM_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    # 기숙사 9 → 생활관 44. 규정 제목 자체가 "생활관 규정"(5-1-7)입니다.
    (("기숙사", "기숙"), ("생활관",)),
    # 교생 27 → 학교현장실습 10 / 교육실습 12. 교직 규정은 "학교현장실습"으로 씁니다.
    (("교생",), ("학교현장실습", "교육실습")),
    # 복전 0 → 복수전공 35. 줄임말이라 코퍼스에 아예 없습니다.
    (("복전",), ("복수전공",)),
    (("부전",), ("부전공",)),
    # 출첵 0 → 출석 81.
    (("출첵",), ("출석",)),
    # 성적컷 0 → 평점평균 45. 장학 기준을 묻는 표현입니다.
    (("성적컷", "성적 컷", "커트라인"), ("평점평균",)),
    # 벌금 3 → 연체료 2. 도서관 규정은 "연체료"로 씁니다.
    (("벌금", "과태료"), ("연체료",)),
    # 짤리다/잘리다 0 → 탈락 9. 장학생 자격 상실을 묻는 구어입니다.
    (("짤리", "짤려", "잘리", "잘려"), ("탈락",)),
    # 알바 3 → 근로장학 4.
    (("알바", "아르바이트"), ("근로장학",)),
    # 군대 → 군입대 3 / 병역 9.
    (("군대", "군입대", "입대"), ("군입대", "병역")),
    # 과 옮기다 → 전과 37.
    (("과 옮기", "과 옮길", "학과 옮기", "학과 바꾸", "과 바꾸"), ("전과",)),
    # 등록금 깎다 → 감면 20 / 면제 36.
    (("깎아", "깎아줘", "깎아주", "할인"), ("감면", "면제")),
)


# (맥락 단어, 행위 단어, 확장어) — 둘 다 있어야 발동하는 규칙입니다.
#
# 왜 필요한가: 2026-08-12 실측에서 "기숙사 중간에 나가려면?"이 동의어 확장 후에도
# 실패했습니다. 원인은 정답 조항인 `5-1-7/제19조의2(퇴사신고)`가 **"생활관"이라는
# 단어를 담고 있지 않아서**입니다. 문서 어휘로 확장하면 그 문서의 **다른 조항들**이
# 올라올 뿐, 정작 답이 있는 조항에는 닿지 못합니다.
#
#     SELECT content LIKE '%생활관%', content LIKE '%퇴사%'
#       FROM document_chunks_1024
#      WHERE metadata->>'doc_id'='5-1-7' AND metadata->>'article'='제19조의2';
#     -- f | t
#
# 그래서 "무엇에 대한 질문인가"(기숙사)가 아니라 "무엇을 하려는가"(나가다 → 퇴사)를
# 조항 어휘로 옮겨야 합니다. 맥락 없이 "나가다 → 퇴사"만 두면 전혀 다른 질문까지
# 퇴사 조항으로 끌고 가므로, 두 조건을 함께 요구합니다.
CONTEXT_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    # 기숙사에서 나가다 → 퇴사 9청크 (5-1-7 제19조/제19조의2)
    (("기숙사", "생활관", "기숙"), ("나가", "나갈", "나오", "그만"), ("퇴사",)),
    # 기숙사에 들어가다 → 입사 14청크
    (("기숙사", "생활관", "기숙"), ("들어가", "들어갈", "살", "신청"), ("입사",)),
)


# tsquery에 그대로 넘길 값이라 한글·영숫자만 남깁니다. 확장어는 우리가 정의한
# 상수지만, 사전을 늘릴 때 실수로 연산자 문자가 들어가면 구문 오류가 납니다.
_SAFE = re.compile(r"[^0-9A-Za-z가-힣]+")


def lexical_expansion_terms(query_text: str) -> tuple[str, ...]:
    """질의에서 감지된 규정 어휘를 돌려줍니다. 어휘(FTS) 검색 OR 확장용입니다.

    질의에 이미 들어 있는 단어는 빼고 돌려줍니다. `plainto_tsquery(원문)`이 이미
    잡고 있어서 중복 OR가 되기 때문입니다.

    Args:
        query_text (str): 사용자 질의문

    Returns:
        tuple[str, ...]: OR로 함께 찾을 규정 어휘. 없으면 빈 튜플
    """

    if not query_text:
        return ()

    found: list[str] = []

    def add(terms: tuple[str, ...]) -> None:
        for term in terms:
            safe = _SAFE.sub("", term)
            if safe and safe not in query_text and safe not in found:
                found.append(safe)

    for triggers, expansions in SYNONYM_RULES:
        if any(t in query_text for t in triggers):
            add(expansions)

    for contexts, actions, expansions in CONTEXT_RULES:
        if any(c in query_text for c in contexts) and any(a in query_text for a in actions):
            add(expansions)

    return tuple(found)


def expand_for_embedding(query_text: str) -> str:
    """dense 임베딩용으로 규정 어휘를 덧붙인 질의문을 만듭니다.

    어휘 검색과 달리 임베딩은 AND/OR 개념이 없어서 그냥 이어 붙이면 됩니다.
    다만 **의미를 바꾸는 개입**이라 어휘 확장과 효과를 반드시 따로 재야 합니다.
    (`docs/kmu_ai_eval.md` S3의 ablation)

    Args:
        query_text (str): 사용자 질의문

    Returns:
        str: 확장된 질의문. 확장할 것이 없으면 원문 그대로
    """

    terms = lexical_expansion_terms(query_text)
    if not terms:
        return query_text
    return f"{query_text} {' '.join(terms)}"


def _selftest() -> int:
    """DB 없이 도는 자체 검증. `python3 -m app.utils.query_synonyms`"""

    cases: list[tuple[str, tuple[str, ...]]] = [
        # 맥락 규칙: 기숙사 + 나가다 → 퇴사까지 함께 나와야 합니다.
        ("기숙사 중간에 나가려면요?", ("생활관", "퇴사")),
        ("기숙사 들어가려면 어떻게 해요?", ("생활관", "입사")),
        # 맥락 단어만 있고 행위 단어가 없으면 맥락 규칙은 발동하지 않습니다.
        ("기숙사 벌점은 뭘 기준으로 매겨?", ("생활관",)),
        # 행위 단어만 있고 맥락이 없으면 엉뚱한 확장을 하지 않아야 합니다.
        ("수업 중간에 나가도 돼요?", ()),
        ("교생 몇주 나가요?", ("학교현장실습", "교육실습")),
        ("복전 몇학점 들어야됨?", ("복수전공",)),
        ("장학금 성적컷 얼마임?", ("평점평균",)),
        ("책 늦게 반납하면 벌금 내요?", ("연체료",)),
        ("정학 먹으면 장학금 짤리나요", ("탈락",)),
        ("과 옮길 수 있어요?", ("전과",)),
        # 확장 대상이 질의에 이미 있으면 중복으로 넣지 않습니다.
        ("생활관 퇴사 절차 알려줘", ()),
        # 해당 없는 질의는 건드리지 않습니다.
        ("졸업학점이 몇 학점이야?", ()),
        ("", ()),
    ]

    failed = 0
    for query, expected in cases:
        got = lexical_expansion_terms(query)
        if got != expected:
            print(f"  FAIL {query!r}: {got} != {expected}")
            failed += 1

    # 확장어에 tsquery 연산자가 섞이면 안 됩니다.
    for _, expansions in SYNONYM_RULES:
        for term in expansions:
            if _SAFE.sub("", term) != term:
                print(f"  FAIL 확장어에 안전하지 않은 문자: {term!r}")
                failed += 1

    # 임베딩 확장은 원문을 보존해야 합니다.
    q = "기숙사 중간에 나가려면요?"
    if not expand_for_embedding(q).startswith(q):
        print("  FAIL expand_for_embedding이 원문을 보존하지 않음")
        failed += 1
    if expand_for_embedding("졸업학점이 몇 학점이야?") != "졸업학점이 몇 학점이야?":
        print("  FAIL 확장할 것이 없는데 질의가 바뀜")
        failed += 1

    print(f"query_synonyms 자체 검증: {'통과' if not failed else f'{failed}건 실패'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
