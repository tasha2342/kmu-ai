"""학생용 골드셋(questions_student.jsonl)의 스키마와 검증.

기존 48문항(eval/questions.jsonl)은 날짜/표 추출 전용이라 필드가 13개였습니다.
학생 문항은 자유형이 섞여 있어 8개를 더 씁니다. 늘어난 이유는 필드별 주석에 적었습니다.

**기존 13필드의 의미는 그대로 둡니다.** scoring.score_retrieval()이 doc_id +
must_retrieve_keywords를, score_answer()가 answer/acceptable_answers/normalize를
그대로 요구하므로, 문서 단위 지표가 조항 단위 지표와 나란히 계속 산출됩니다.
과거 리포트의 Recall@12 0.854와 비교 가능한 축을 잃지 않기 위한 조치입니다.

검증은 이 개발 머신에서 돕니다(stdlib만 씁니다).

    python3 -m eval.schema --validate eval/questions_student.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# 값 도메인
# ---------------------------------------------------------------------------

# ID 접두사 → 카테고리. 문항 문서(docs/kmu_ai_chatbot_test_questions.md)의 절과 1:1입니다.
CATEGORIES: dict[str, str] = {
    "HAK": "학칙 총론 — 수업연한·학기·휴업일·조기졸업·재학연한",
    "SUG": "수강·이수·학점·성적·시험",
    "HYU": "휴학·복학·퇴학·제적·전과",
    "JOL": "졸업요건·복수전공·융합전공·교직",
    "JAN": "장학",
    "SAE": "생활관·도서관·시설",
    "IN": "인권·징계·포상·학생활동",
    "GUK": "외국인유학생·교환학생",
    "BOK": "학생지원 — 장애학생·상담·보건",
    "NEG": "부정 대조군 — 답하면 안 되는 것",
    "TRIG": "검색 트리거 대조군 — 구어체·오타",
}

# 정답의 논리 구조. normalize(값의 형식)와는 직교합니다.
#   exact       하나의 값
#   list_all    required_facts를 전부 말해야 정답
#   list_any    나열 중 하나만 맞아도 정답
#   conditional 경우에 따라 달라지는 답 (조건과 결과가 같이 나와야 함)
#   abstain     근거가 없으므로 모른다고 답해야 정답
ANSWER_TYPES = ("exact", "list_all", "list_any", "conditional", "abstain")

# 값의 형식. 기존 scoring.score_answer()의 분기와 같은 집합입니다.
NORMALIZE_TYPES = ("date", "number", "percent", "list", "text", "unknown")

UNANSWERABLE_REASONS = (
    "not_in_corpus",  # 규정집에 없는 사실 (학사일정, 등록금액, 식단)
    "personal_data",  # 개인 학적 조회 (내 학점, 내 장학금)
    "out_of_scope",  # 대학과 무관한 일반 질문
    "time_sensitive",  # 지금 시점에 따라 달라지는 것 (오늘 도서관 여나요)
)

# 조항 단위 정답 키에 쓰면 안 되는 article 값.
# 학칙 2-0-1의 '부칙'은 청크 80개에 걸쳐 있어서 정답 키로 쓰면 아무 의미가 없습니다.
UNUSABLE_ARTICLES = {"부칙", "문서메타", None, ""}

# gold_articles 하나가 이 개수를 넘는 청크로 흩어지면 오라클 컨텍스트가 비대해집니다.
MAX_CHUNKS_PER_GOLD_ARTICLE = 4


@dataclass
class GoldRecord:
    """골드 문항 하나. 21필드."""

    # --- 기존 13필드 (eval/questions.jsonl과 동일 의미) ---
    id: str
    type: str  # date / table / rule  ← 학생 문항은 대부분 rule
    subtype: str
    doc_id: Optional[str]
    article: Optional[str]
    question: str
    answer: str
    acceptable_answers: list[str]
    evidence: str
    must_retrieve_keywords: list[str]
    normalize: str
    table_id: Optional[str] = None
    notes: str = ""

    # --- 신규 8필드 ---

    # 조항 단위 recall의 정답 키. [{"doc_id": "2-0-1", "article": "제33조"}, ...]
    # (doc_id, article)이 사실상 안정적 청크 ID입니다 — 부칙을 뺀 3,967개 키 중
    # 청크 2개 이상으로 쪼개지는 것은 74개(1.9%)뿐이라 별도 chunk_id가 필요 없습니다.
    gold_articles: list[dict[str, str]] = field(default_factory=list)

    answer_type: str = "exact"

    # 정답의 근거가 되는 원문 구절. gold_builder가 조문에서 그대로 떠 옵니다.
    # LLM-judge의 판정 기준이자 revalidate_gold의 검증 대상입니다.
    gold_spans: list[str] = field(default_factory=list)

    required_facts: list[str] = field(default_factory=list)  # list_all에서 전부 필요
    forbidden_facts: list[str] = field(default_factory=list)  # 있으면 환각
    unanswerable_reason: Optional[str] = None  # NEG-* 전용
    category: str = ""  # ID 접두사. 층화 집계용
    judge: bool = False  # LLM 판정 필요 여부

    @property
    def prefix(self) -> str:
        return self.id.rsplit("-", 1)[0]


def normalize_text(text: str) -> str:
    """비교용 정규화. scoring.normalize_text와 같은 규칙입니다."""
    return unicodedata.normalize("NFC", text or "").strip()


def load_questions(path: str | Path) -> list[dict[str, Any]]:
    """JSONL 골드셋을 읽습니다. 빈 줄과 # 주석줄은 건너뜁니다."""
    records: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as fp:
        for lineno, line in enumerate(fp, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno} JSON 파싱 실패: {exc}") from exc
    return records


def to_record(raw: dict[str, Any]) -> GoldRecord:
    known = {f for f in GoldRecord.__dataclass_fields__}
    return GoldRecord(**{k: v for k, v in raw.items() if k in known})


# ---------------------------------------------------------------------------
# 검증
# ---------------------------------------------------------------------------


def _article_index(chunks_path: Optional[Path]) -> Optional[dict[tuple[str, str], int]]:
    """빌드된 인덱스에서 (doc_id, article) → 청크 수를 만듭니다.

    인덱스가 없으면 None을 돌려주고, 조항 실재 검사는 건너뜁니다.
    (인덱스를 아직 안 만든 상태에서도 형식 검사는 되어야 하므로.)
    """
    if chunks_path is None or not chunks_path.exists():
        return None
    counts: Counter[tuple[str, str]] = Counter()
    with chunks_path.open(encoding="utf-8") as fp:
        for line in fp:
            chunk = json.loads(line)
            doc_id, article = chunk.get("doc_id"), chunk.get("article")
            if doc_id and article:
                counts[(doc_id, normalize_text(article))] += 1
    return dict(counts)


def validate(
    records: list[dict[str, Any]],
    chunks_path: Optional[Path] = None,
) -> tuple[list[str], list[str]]:
    """골드셋을 검사합니다.

    Returns:
        (errors, warnings) — errors가 비어 있어야 실험에 쓸 수 있습니다.
    """
    errors: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    articles = _article_index(chunks_path)

    for raw in records:
        qid = raw.get("id") or "<id 없음>"

        # --- 식별자 ---
        if qid in seen_ids:
            errors.append(f"{qid}: ID 중복")
        seen_ids.add(qid)

        rec = to_record(raw)
        prefix = rec.prefix
        if prefix not in CATEGORIES:
            errors.append(f"{qid}: 알 수 없는 ID 접두사 {prefix!r} (허용: {', '.join(CATEGORIES)})")
        elif rec.category and rec.category != prefix:
            errors.append(f"{qid}: category={rec.category!r}가 ID 접두사 {prefix!r}와 불일치")

        # --- 값 도메인 ---
        if rec.answer_type not in ANSWER_TYPES:
            errors.append(f"{qid}: answer_type={rec.answer_type!r} 허용되지 않음")
        if rec.normalize not in NORMALIZE_TYPES:
            errors.append(f"{qid}: normalize={rec.normalize!r} 허용되지 않음")
        if not rec.question.strip():
            errors.append(f"{qid}: question이 비어 있음")

        # --- answer_type ↔ normalize 정합성 ---
        # abstain 문항은 모른다고 답해야 정답이므로 정규화기도 unknown이어야 합니다.
        # 이게 어긋나면 채점기가 기권을 오답으로 셉니다.
        if rec.answer_type == "abstain" and rec.normalize != "unknown":
            errors.append(f"{qid}: answer_type=abstain인데 normalize={rec.normalize!r} (unknown이어야 함)")
        if rec.normalize == "unknown" and rec.answer_type != "abstain":
            errors.append(f"{qid}: normalize=unknown인데 answer_type={rec.answer_type!r} (abstain이어야 함)")
        if rec.answer_type == "list_all" and not rec.required_facts:
            errors.append(f"{qid}: answer_type=list_all인데 required_facts가 비어 있음")

        # --- 기권 문항 ---
        if rec.answer_type == "abstain":
            if rec.gold_articles:
                errors.append(f"{qid}: 기권 문항인데 gold_articles가 채워져 있음")
            if rec.unanswerable_reason not in UNANSWERABLE_REASONS:
                errors.append(
                    f"{qid}: unanswerable_reason={rec.unanswerable_reason!r} 허용되지 않음 "
                    f"(허용: {', '.join(UNANSWERABLE_REASONS)})"
                )
        else:
            if not rec.gold_articles:
                errors.append(f"{qid}: gold_articles가 비어 있음 (기권 문항이 아니면 필수)")
            if rec.unanswerable_reason is not None:
                errors.append(f"{qid}: 기권 문항이 아닌데 unanswerable_reason이 채워져 있음")
            if not rec.answer.strip():
                errors.append(f"{qid}: answer가 비어 있음")

        # --- gold_articles 형식과 실재 ---
        for ga in rec.gold_articles:
            if not isinstance(ga, dict) or "doc_id" not in ga or "article" not in ga:
                errors.append(f"{qid}: gold_articles 항목 형식 오류: {ga!r}")
                continue
            art = normalize_text(ga["article"])
            if art in UNUSABLE_ARTICLES:
                errors.append(
                    f"{qid}: gold_articles에 {art!r} 사용 불가 — 문서 전체에 흩어져 있어 "
                    f"정답 키가 되지 못합니다"
                )
                continue
            if articles is None:
                continue
            key = (ga["doc_id"], art)
            n = articles.get(key)
            if n is None:
                errors.append(f"{qid}: 코퍼스에 없는 조항 {ga['doc_id']} {art}")
            elif n > MAX_CHUNKS_PER_GOLD_ARTICLE:
                warnings.append(f"{qid}: {ga['doc_id']} {art}가 청크 {n}개로 흩어짐 — 오라클 컨텍스트가 커집니다")

        # --- 검색 지표용 필드 (문서 단위 지표 유지에 필요) ---
        if rec.answer_type != "abstain":
            if not rec.doc_id:
                errors.append(f"{qid}: doc_id가 없음 — 문서 단위 recall을 못 냅니다")
            if not rec.must_retrieve_keywords:
                errors.append(f"{qid}: must_retrieve_keywords가 비어 있음")
            # doc_id는 gold_articles의 주 문서와 같아야 합니다.
            gold_docs = {ga.get("doc_id") for ga in rec.gold_articles if isinstance(ga, dict)}
            if rec.doc_id and gold_docs and rec.doc_id not in gold_docs:
                errors.append(f"{qid}: doc_id={rec.doc_id!r}가 gold_articles의 문서 {gold_docs}에 없음")

        # --- judge 플래그 ---
        # 자유형 정답은 정규식으로 못 채점합니다. judge=false면 그 문항은 사실상 항상 오답 처리됩니다.
        if rec.answer_type in ("list_all", "conditional") and not rec.judge:
            warnings.append(f"{qid}: answer_type={rec.answer_type}인데 judge=false — 정규화기가 채점하기 어렵습니다")

        # --- 근거 ---
        if rec.answer_type != "abstain" and not rec.gold_spans:
            warnings.append(f"{qid}: gold_spans가 비어 있음 — LLM-judge가 근거 없이 판정하게 됩니다")

    return errors, warnings


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """골드셋 구성 집계. 카테고리 편중을 눈으로 확인하는 용도입니다."""
    cats = Counter(to_record(r).prefix for r in records)
    return {
        "n": len(records),
        "scored": sum(1 for r in records if to_record(r).prefix != "TRIG"),
        "by_category": dict(sorted(cats.items())),
        "by_answer_type": dict(Counter(r.get("answer_type", "exact") for r in records)),
        "by_normalize": dict(Counter(r.get("normalize", "text") for r in records)),
        "judge_required": sum(1 for r in records if r.get("judge")),
        "documents_covered": len(
            {ga["doc_id"] for r in records for ga in r.get("gold_articles", []) if ga.get("doc_id")}
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="학생용 골드셋 검증")
    parser.add_argument("--validate", metavar="PATH", help="검증할 JSONL 경로")
    parser.add_argument(
        "--index",
        default="L_full_typed",
        help="조항 실재 검사에 쓸 인덱스 이름 (기본 L_full_typed). none이면 건너뜁니다",
    )
    parser.add_argument("--summary", action="store_true", help="구성 집계도 출력")
    args = parser.parse_args()

    if not args.validate:
        parser.error("--validate PATH 가 필요합니다")

    records = load_questions(args.validate)

    chunks_path: Optional[Path] = None
    if args.index != "none":
        chunks_path = Path(__file__).resolve().parent / "indexes" / args.index / "chunks.jsonl"
        if not chunks_path.exists():
            print(f"[경고] 인덱스 {args.index}에 chunks.jsonl이 없어 조항 실재 검사를 건너뜁니다", file=sys.stderr)
            chunks_path = None

    errors, warnings = validate(records, chunks_path)

    if args.summary or not errors:
        print(json.dumps(summarize(records), ensure_ascii=False, indent=2))

    for w in warnings:
        print(f"[경고] {w}", file=sys.stderr)
    for e in errors:
        print(f"[오류] {e}", file=sys.stderr)

    print(f"\n{len(records)}문항 · 오류 {len(errors)} · 경고 {len(warnings)}", file=sys.stderr)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
