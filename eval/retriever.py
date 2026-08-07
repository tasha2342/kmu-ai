"""평가용 인메모리 검색기.

pgvector 없이 돌립니다. DB를 띄우지 않고도 검색 품질을 잴 수 있어야 하기 때문입니다.

## 백엔드 4종

| 이름 | 내용 | 왜 있나 |
| --- | --- | --- |
| `pg_simple` | PostgreSQL `to_tsvector('simple')` + `ts_rank_cd` 근사 | **현재 프로덕션의 어휘 검색** |
| `lexical` | 한국어 토크나이저 BM25 + 조항/별표 가산점 | rag-test가 91.7%를 낸 어휘 검색 |
| `dense` | KURE-v1 코사인 | 임베딩만의 성능 |
| `hybrid` | 프로덕션 융합식 `0.55*dense + 0.45*lexical + boost` | 최종 구성 |

`pg_simple`과 `lexical`을 나란히 두는 이유가 실험 R5입니다. 프로덕션 어휘 검색은
`simple` 설정이라 형태소 분석이 없어 `규정은`/`규정을`/`규정이`가 전부 다른 토큰입니다
(app/utils/vector_store.py의 58~72행에 한계가 문서화돼 있습니다).
8문서에서는 dense가 이 약점을 가려 줬지만 181문서에서도 그런지는 재 봐야 압니다.

## 프로덕션 코드 재사용

`hybrid` 백엔드는 융합·가산점을 `app.utils.vector_store`에서 **그대로 import** 합니다.
여기서 별도 구현을 만들면 측정값이 프로덕션 값이 아니게 됩니다.
그 모듈은 peewee를 요구하므로 의존성이 없는 개발 머신에서는 `hybrid`/`dense`를 쓸 수 없고
`lexical`/`pg_simple`만 동작합니다. 전체 실험은 H200에서 돌립니다.
"""

from __future__ import annotations

import json
import math
import re

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from eval.router import Route


# rag-test가 쓴 토크나이저. 한글 어절과 함께 `제15조`, `별표 6-1`을 하나의 토큰으로 잡습니다.
BM25_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+|제\d+조(?:의\d+)?|별표\s*[\d\-]+")

# PostgreSQL `simple` 파서 근사. 한글은 형태소 분석 없이 어절 단위로 끊깁니다.
PG_SIMPLE_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")

# BM25 파라미터. rag-test와 동일.
BM25_K1 = 1.5
BM25_B = 0.75

# 어휘 검색 안에서 주는 정확 일치 가산점. rag-test/eval/retriever.py와 동일 값.
ARTICLE_EXACT_BONUS = 2.0
ATTACHMENT_BONUS = 1.5

# 제목 기반 문서 라우팅 가산점 (실험 R6).
# rag-test는 9개 문서 제목을 하드코딩했는데, 181문서에서는 그 방식이 확장되지 않습니다.
TITLE_ROUTE_STRONG = 0.15
TITLE_ROUTE_WEAK = 0.05


def bm25_tokens(text: str) -> list[str]:
    return [t.lower() for t in BM25_TOKEN_RE.findall(text or "")]


def pg_simple_tokens(text: str) -> list[str]:
    return [t.lower() for t in PG_SIMPLE_TOKEN_RE.findall(text or "")]


class _InvertedIndex:
    """토큰 → (청크 인덱스, 빈도) 역색인. 4,900청크 × 48질의를 순수 파이썬으로 돌리기 위한 것."""

    def __init__(self, docs: list[list[str]]):
        self.doc_len = [len(d) for d in docs]
        self.count = len(docs)
        self.avg_len = sum(self.doc_len) / max(1, self.count)
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for i, tokens in enumerate(docs):
            for token, freq in Counter(tokens).items():
                self.postings[token].append((i, freq))


class EvalRetriever:
    """인덱스 하나를 로드해 질의를 처리합니다."""

    _boost_warned = False

    def __init__(self, index_dir: str | Path, backend: str = "lexical"):
        self.index_dir = Path(index_dir)
        self.backend = backend

        with (self.index_dir / "chunks.jsonl").open(encoding="utf-8") as f:
            self.chunks: list[dict[str, Any]] = [json.loads(line) for line in f]

        self._bm25 = _InvertedIndex([bm25_tokens(c.get("text", "")) for c in self.chunks])
        self._pg = _InvertedIndex([pg_simple_tokens(c.get("text", "")) for c in self.chunks])

        # 문서 제목 토큰 (제목 기반 라우팅용). 파일명에서 문서번호와 확장자를 뺀 부분.
        self._title_tokens: list[set[str]] = []
        for chunk in self.chunks:
            name = str(chunk.get("file_name") or "")
            title = re.sub(r"^\d+-\d+-\d+", "", name)
            title = re.sub(r"\(.*?\)|\.hwp$", "", title).replace("+", " ")
            self._title_tokens.append(set(bm25_tokens(title)))

        self.embeddings: Optional[Any] = None
        embed_path = self.index_dir / "embeddings.npy"
        if backend in ("dense", "hybrid"):
            if not embed_path.exists():
                raise FileNotFoundError(
                    f"{backend} 백엔드에는 임베딩이 필요합니다: {embed_path}\n"
                    "  eval.index --with-embeddings 로 먼저 빌드하세요."
                )
            import numpy as np

            self.embeddings = np.load(embed_path)
            if len(self.embeddings) != len(self.chunks):
                raise ValueError(
                    f"청크 {len(self.chunks)}개와 임베딩 {len(self.embeddings)}개가 어긋납니다."
                )
            self._embedder = None  # 첫 질의에서 지연 로드

    # ── 어휘 검색 ────────────────────────────────────────────────────────────

    def _bm25_scores(self, query: str) -> dict[int, float]:
        index = self._bm25
        scores: dict[int, float] = defaultdict(float)
        terms = bm25_tokens(query)

        for term in terms:
            postings = index.postings.get(term)
            if not postings:
                continue
            idf = math.log(1 + (index.count - len(postings) + 0.5) / (len(postings) + 0.5))
            for i, freq in postings:
                denom = freq + BM25_K1 * (1 - BM25_B + BM25_B * index.doc_len[i] / index.avg_len)
                scores[i] += idf * (freq * (BM25_K1 + 1)) / denom

        # 조항/별표 정확 일치 가산점. 본문이 아니라 메타데이터를 봅니다.
        articles = {t for t in terms if t.startswith("제") and "조" in t}
        attachments = {t.replace(" ", "") for t in terms if t.startswith("별표")}
        if articles or attachments:
            for i in list(scores):
                chunk = self.chunks[i]
                if articles and (chunk.get("article") or "") in articles:
                    scores[i] += ARTICLE_EXACT_BONUS
                if attachments:
                    body = (chunk.get("text") or "").replace(" ", "")
                    if any(a in body for a in attachments):
                        scores[i] += ATTACHMENT_BONUS

        return scores

    def _pg_simple_scores(self, query: str) -> dict[int, float]:
        """PostgreSQL `to_tsvector('simple') @@ plainto_tsquery` + `ts_rank_cd` 근사.

        `plainto_tsquery`는 질의어를 AND로 묶으므로, **모든 질의 토큰을 포함한 청크만**
        후보가 됩니다. 이게 형태소 분석 없는 `simple` 설정의 진짜 문제입니다 —
        `규정은`으로 물으면 `규정을`이라고 쓰인 청크는 후보에도 못 듭니다.
        """

        index = self._pg
        terms = [t for t in pg_simple_tokens(query) if t]
        if not terms:
            return {}

        candidate: Optional[set[int]] = None
        for term in set(terms):
            hits = {i for i, _ in index.postings.get(term, [])}
            candidate = hits if candidate is None else (candidate & hits)
            if not candidate:
                return {}

        scores: dict[int, float] = {}
        for i in candidate or set():
            total = 0.0
            for term in set(terms):
                freq = next((f for j, f in index.postings.get(term, []) if j == i), 0)
                if freq:
                    total += freq / (1.0 + math.log(1 + index.doc_len[i]))
            scores[i] = total
        return scores

    # ── dense ───────────────────────────────────────────────────────────────

    def _dense_scores(self, query: str) -> dict[int, float]:
        import numpy as np

        if self._embedder is None:
            from eval.index import GpuEmbedder

            self._embedder = GpuEmbedder()

        vector = np.asarray(self._embedder.encode([query])[0], dtype="float32")
        sims = self.embeddings @ vector
        return {i: float(sims[i]) for i in range(len(sims))}

    # ── 라우팅 가산점 ───────────────────────────────────────────────────────

    def _title_route_boosts(self, query: str) -> dict[int, float]:
        """질의어와 문서 제목의 어휘 겹침으로 문서를 좁힙니다. (R6)

        rag-test는 문서 9개를 제목 키워드 표에 하드코딩했습니다. 181문서에서는
        표를 손으로 유지할 수 없으므로 제목 토큰 겹침으로 일반화합니다.
        """

        query_tokens = set(bm25_tokens(query))
        boosts: dict[int, float] = {}
        for i, title in enumerate(self._title_tokens):
            overlap = len(query_tokens & title)
            if overlap >= 2:
                boosts[i] = TITLE_ROUTE_STRONG
            elif overlap == 1:
                boosts[i] = TITLE_ROUTE_WEAK
        return boosts

    # ── 검색 ────────────────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        top_k: int = 12,
        route: Optional[Route] = None,
        title_routing: bool = False,
        section_filter: Optional[dict[str, str]] = None,
    ) -> list[dict[str, Any]]:
        """질의 하나에 대한 상위 top_k 청크."""

        if self.backend == "lexical":
            raw = self._bm25_scores(query)
        elif self.backend == "pg_simple":
            raw = self._pg_simple_scores(query)
        elif self.backend == "dense":
            raw = self._dense_scores(query)
        elif self.backend == "hybrid":
            raw = self._hybrid_scores(query, top_k)
        else:
            raise ValueError(f"알 수 없는 백엔드: {self.backend}")

        if not raw:
            return []

        # 정규화 후 가산점. hybrid는 이미 정규화·가산점이 들어간 점수입니다.
        if self.backend != "hybrid":
            highest = max(raw.values()) or 1.0
            scores = {i: v / highest for i, v in raw.items()}
            scores = self._apply_signal_boosts(query, scores)
        else:
            scores = dict(raw)

        if title_routing:
            for i, boost in self._title_route_boosts(query).items():
                if i in scores:
                    scores[i] += boost

        if section_filter:
            key, value = next(iter(section_filter.items()))
            scores = {i: s for i, s in scores.items() if str(self.chunks[i].get(key)) == value}
            if not scores:
                return []

        ordered = sorted(scores.items(), key=lambda kv: -kv[1])[:top_k]
        return [{**self.chunks[i], "score": score} for i, score in ordered]

    def _apply_signal_boosts(self, query: str, scores: dict[int, float]) -> dict[int, float]:
        """프로덕션 `parse_query_signals` + `compute_boost`를 그대로 적용합니다.

        `app.utils.vector_store`는 peewee를 요구합니다. 의존성이 없는 개발 머신에서는
        가산점 없이 진행하고 한 번만 경고합니다. **실험 수치는 의존성이 갖춰진
        환경(H200)에서 낸 것만 유효합니다.** 여기서 나온 값은 스모크 테스트용입니다.
        """

        try:
            from app.utils.vector_store import compute_boost, parse_query_signals
        except ModuleNotFoundError as exc:
            if not EvalRetriever._boost_warned:
                print(f"[경고] 정확 일치 가산점을 건너뜁니다 ({exc}). 스모크 테스트 전용 결과입니다.")
                EvalRetriever._boost_warned = True
            return scores

        signals = parse_query_signals(query)
        if not signals["doc_ids"] and not signals["articles"]:
            return scores
        return {i: s + compute_boost(self.chunks[i], signals) for i, s in scores.items()}

    def _hybrid_scores(self, query: str, top_k: int) -> dict[int, float]:
        """프로덕션 융합식으로 dense와 어휘 점수를 합칩니다.

        후보를 `top_k * CANDIDATE_MULTIPLIER` 로 자른 뒤 융합하는 것까지 프로덕션과 같게
        맞춥니다. min-max 정규화는 후보군 크기에 민감해서(각 후보군의 최하위가 정확히 0이 됨)
        전체 청크를 후보로 넣으면 프로덕션과 다른 순위가 나옵니다.
        """

        from app.utils.vector_store import (
            CANDIDATE_MULTIPLIER,
            compute_boost,
            fuse_hybrid_scores,
            parse_query_signals,
        )

        candidate_limit = max(1, top_k * CANDIDATE_MULTIPLIER)
        dense = self._top_n(self._dense_scores(query), candidate_limit)
        lexical = self._top_n(self._bm25_scores(query), candidate_limit)

        # fuse_hybrid_scores는 id 문자열 키를 받습니다. 인덱스를 문자열로 씁니다.
        dense_map = {str(i): v for i, v in dense.items()}
        lexical_map = {str(i): v for i, v in lexical.items()}

        signals = parse_query_signals(query)
        boosts = {
            str(i): compute_boost(self.chunks[i], signals)
            for i in set(dense) | set(lexical)
        }

        fused = fuse_hybrid_scores(
            dense_scores=dense_map,
            lexical_scores=lexical_map,
            boosts=boosts,
            limit=0,  # 0이면 자르지 않습니다. 자르기는 retrieve()가 합니다.
        )
        return {int(item["id"]): float(item["score"]) for item in fused}

    @staticmethod
    def _top_n(scores: dict[int, float], n: int) -> dict[int, float]:
        if len(scores) <= n:
            return scores
        return dict(sorted(scores.items(), key=lambda kv: -kv[1])[:n])
