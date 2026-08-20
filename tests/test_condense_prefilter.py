"""`app/utils/chat_graph.py`의 후속 질문 재작성 프리필터(`_needs_condense`) 테스트입니다.

이 프리필터는 **호출을 건너뛰는** 최적화라, 틀리는 두 방향의 무게가 다릅니다.

- 건너뛰지 말았어야 하는데 건너뛰면 → 후속 질문의 맥락이 복원되지 않습니다 (KAI-REQ-015).
- 건너뛸 수 있었는데 호출하면 → 지연만 조금 늘고 동작은 정상입니다.

그래서 아래 테스트는 **놓침(false skip)을 절대 허용하지 않고**, 과호출은 허용하되
비율만 지켜봅니다. 프리필터가 아무것도 못 거르면 최적화가 무의미해지기 때문입니다.
"""

import pytest

from app.utils.chat_graph import _needs_condense


NEEDS_CONDENSE = [
    # 지시어로 앞 대화를 가리키는 발화
    "그럼 기간은 얼마나 되나요?",
    "그거 신청 방법 알려줘",
    "그중에 제일 빠른건?",
    "거기 연락처는?",
    "방금 말한 그 서류 어디에 내나요?",
    "위에서 말한 조건 다시 정리해줘",
    "아까 그 장학금 얘기 좀 더 해줘",
    "그때는 어떻게 해?",
    # 주어가 통째로 생략된 짧은 발화
    "기간은 얼마나 되나요?",
    "얼마나 걸려?",
    "신청은 언제까지?",
    # 영어 (KAI-REQ-029 다국어)
    "What about the deadline?",
    "How long does it take?",
    "Can I apply for that?",
    "Is there a limit on those?",
]
"""반드시 재작성을 호출해야 하는 발화. 하나라도 건너뛰면 맥락 복원이 깨집니다."""

SELF_CONTAINED = [
    "2026학년도 1학기 수강신청 기간이 언제야?",
    "졸업하려면 학점 몇 점 들어야 해?",
    "기숙사 입사와 퇴사 절차가 어떻게 되나요?",
    "국가장학금 신청 자격을 알려줘",
    "교내 채용 설명회 일정 알려줘",
    "학생 징계 절차와 종류를 정리해줘",
    "교직원 보수 규칙의 주요 내용을 요약해줘",
    "시험 망친 것 같아서 너무 불안해",
    "Tell me about the graduation credit requirements",
    "How do I apply for a leave of absence?",
]
"""그 자체로 완결된 발화. 건너뛰어도 안전합니다."""


@pytest.mark.parametrize("query", NEEDS_CONDENSE)
def test_앞_대화에_기대는_발화는_건너뛰지_않는다(query):
    assert _needs_condense(query), f"맥락이 필요한 발화를 건너뜀: {query!r}"


def test_자기완결적_발화를_충분히_걸러낸다():
    """과호출 자체는 안전하지만, 못 거르면 최적화가 없는 것과 같습니다."""

    skipped = [q for q in SELF_CONTAINED if not _needs_condense(q)]
    ratio = len(skipped) / len(SELF_CONTAINED)
    assert ratio >= 0.8, (
        f"자기완결 발화 {len(SELF_CONTAINED)}건 중 {len(skipped)}건만 건너뜀 "
        f"({ratio:.0%}). 프리필터가 거의 걸러내지 못하고 있습니다."
    )


def test_영어_지시어는_단어_경계로_찾는다():
    """부분 문자열로 찾으면 "credit"의 "it" 때문에 전부 호출로 빠집니다."""

    assert not _needs_condense("How many credits do I need to graduate here")
    assert _needs_condense("How many credits do I need for it")


def test_빈_발화는_호출하지_않는다():
    assert not _needs_condense("")
    assert not _needs_condense("   ")
