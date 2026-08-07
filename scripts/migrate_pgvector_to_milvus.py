#!/usr/bin/env python3
"""PostgreSQL pgvector → Milvus dense 벡터 마이그레이션 (읽기 전용 on PG).

기존 FAQ·문서 청크 임베딩을 Milvus로 복사합니다. PG 행은 수정·삭제하지 않습니다.

사용 (API 컨테이너 또는 동일한 configs/config.yaml이 있는 환경):

    # 건수만 확인
    python -m scripts.migrate_pgvector_to_milvus --dry-run

    # 복사
    python -m scripts.migrate_pgvector_to_milvus

    # 복사 후 건수·샘플 검색 검증
    python -m scripts.migrate_pgvector_to_milvus --verify

컷오버는 이 스크립트가 아니라 configs/config.yaml 의 `milvus.enabled: true` 이다.
마이그레이션 중에는 enabled=false 를 유지할 것.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any, Optional

# 프로젝트 루트에서 실행한다고 가정
from app.config import config
from app.utils.database import get_db_manager
from app.utils import milvus_store as milvus_store_mod
from app.models.enum import VectorStatus
import app.models.database as db_models


BATCH_SIZE = 100
VERIFY_SAMPLE_SIZE = 5


def _enum_val(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _as_vector(embedding: Any) -> Optional[list[float]]:
    if embedding is None:
        return None
    if hasattr(embedding, "tolist"):
        return list(embedding.tolist())
    return list(embedding)


async def count_pg_faq(db) -> int:
    from peewee import fn

    query = (db_models.FaqEmbedding
             .select(fn.COUNT(db_models.FaqEmbedding.id).alias("count"))
             .where(db_models.FaqEmbedding.vector_status == VectorStatus.INDEXED.value)
             .where(db_models.FaqEmbedding.embedding.is_null(False)))
    rows = await db.execute_query(query)
    return int(next((r.count for r in rows), 0))


async def count_pg_chunks(db, model) -> int:
    from peewee import fn

    query = model.select(fn.COUNT(model.id).alias("count"))
    rows = await db.execute_query(query)
    return int(next((r.count for r in rows), 0))


async def migrate_faq(db, store: milvus_store_mod.MilvusStore, *, dry_run: bool) -> int:
    dim = db_models.FAQ_EMBEDDING_DIM
    total = await count_pg_faq(db)
    print(f"[faq] PG INDEXED rows with embedding: {total}")
    if dry_run or total == 0:
        return total

    store.ensure_faq_collection(dim)
    offset = 0
    migrated = 0
    while True:
        query = (db_models.FaqEmbedding
                 .select()
                 .where(db_models.FaqEmbedding.vector_status == VectorStatus.INDEXED.value)
                 .where(db_models.FaqEmbedding.embedding.is_null(False))
                 .order_by(db_models.FaqEmbedding.faq_id)
                 .offset(offset)
                 .limit(BATCH_SIZE))
        rows = list(await db.execute_query(query, timeout=120.0))
        if not rows:
            break

        for row in rows:
            vector = _as_vector(row.embedding)
            fields = {
                "status": _enum_val(row.status),
                "vector_status": _enum_val(row.vector_status),
                "language": _enum_val(row.language),
                "category_code": row.category_code,
                "visibility": _enum_val(row.visibility),
                "department_code": row.department_code,
                "question": row.question,
                "source_url": row.source_url,
                "tags": row.tags or [],
            }
            entity = milvus_store_mod.faq_fields_to_entity(
                faq_id=row.faq_id, embedding=vector, fields=fields,
            )
            if entity:
                store.upsert_faq(dim, entity)
                migrated += 1

        offset += len(rows)
        print(f"[faq] upserted {migrated}/{total}")

    return migrated


async def migrate_document_chunks(db, store: milvus_store_mod.MilvusStore, *, dry_run: bool) -> dict[int, int]:
    counts: dict[int, int] = {}
    for vector_size, model in db_models.DOCUMENT_CHUNK_MODELS.items():
        total = await count_pg_chunks(db, model)
        counts[vector_size] = total
        print(f"[chunks_{vector_size}] PG rows: {total}")
        if dry_run or total == 0:
            continue

        store.ensure_document_collection(vector_size)
        offset = 0
        migrated = 0
        while True:
            query = (model
                     .select()
                     .order_by(model.id)
                     .offset(offset)
                     .limit(BATCH_SIZE))
            rows = list(await db.execute_query(query, timeout=120.0))
            if not rows:
                break

            # logical collection_name 별로 묶어 upsert
            by_collection: dict[str, list[dict]] = {}
            for row in rows:
                vector = _as_vector(row.embedding)
                if vector is None:
                    continue
                payload = dict(row.metadata or {})
                payload.update({
                    "document_id": row.document_id,
                    "chunk_index": row.chunk_index,
                    "content": row.content,
                    "page": row.page,
                    "file_name": row.file_name,
                })
                by_collection.setdefault(row.collection_name, []).append({
                    "id": str(row.id),
                    "vector": vector,
                    "payload": payload,
                })

            for collection_name, points in by_collection.items():
                store.upsert_document_chunks(vector_size, collection_name, points)
                migrated += len(points)

            offset += len(rows)
            print(f"[chunks_{vector_size}] upserted {migrated}/{total}")

        counts[vector_size] = migrated
    return counts


async def verify(db, store: milvus_store_mod.MilvusStore) -> int:
    """건수 비교 + 샘플 자기-검색. 실패 건수를 반환한다."""

    failures = 0
    pg_faq = await count_pg_faq(db)
    milvus_faq = store.count_faq()
    print(f"[verify faq] PG={pg_faq} Milvus={milvus_faq}")
    if pg_faq != milvus_faq:
        print("  MISMATCH: FAQ 건수가 다릅니다.")
        failures += 1

    for vector_size, model in db_models.DOCUMENT_CHUNK_MODELS.items():
        pg_n = await count_pg_chunks(db, model)
        milvus_n = store.count_document_chunks(vector_size)
        print(f"[verify chunks_{vector_size}] PG={pg_n} Milvus={milvus_n}")
        if pg_n != milvus_n:
            print(f"  MISMATCH: document_chunks_{vector_size} 건수가 다릅니다.")
            failures += 1

        if pg_n == 0:
            continue

        sample_query = model.select().limit(VERIFY_SAMPLE_SIZE)
        samples = list(await db.execute_query(sample_query, timeout=60.0))
        for row in samples:
            vector = _as_vector(row.embedding)
            if vector is None:
                continue
            hits = store.search_document_chunks(
                vector_size,
                row.collection_name,
                vector,
                limit=5,
            )
            hit_ids = {h["id"] for h in hits}
            if str(row.id) not in hit_ids:
                print(
                    f"  SAMPLE MISS: chunk {row.id} not in top-5 self-search "
                    f"(collection={row.collection_name})"
                )
                failures += 1
            else:
                print(f"  sample ok: {row.id} score={hits[0]['score']:.4f}")

    # FAQ 샘플
    if pg_faq > 0:
        faq_q = (db_models.FaqEmbedding
                 .select()
                 .where(db_models.FaqEmbedding.vector_status == VectorStatus.INDEXED.value)
                 .where(db_models.FaqEmbedding.embedding.is_null(False))
                 .limit(VERIFY_SAMPLE_SIZE))
        for row in await db.execute_query(faq_q, timeout=60.0):
            vector = _as_vector(row.embedding)
            if vector is None:
                continue
            hits = store.search_faq(
                db_models.FAQ_EMBEDDING_DIM,
                vector,
                limit=5,
                score_threshold=None,
            )
            hit_ids = {h["faq_id"] for h in hits}
            # 검색은 published+indexed 필터를 쓰므로 draft면 안 잡힐 수 있다
            if _enum_val(row.status) != "published":
                continue
            if str(row.faq_id) not in hit_ids:
                print(f"  SAMPLE MISS: faq {row.faq_id} not in top-5 self-search")
                failures += 1
            else:
                print(f"  faq sample ok: {row.faq_id}")

    return failures


async def main_async(args: argparse.Namespace) -> int:
    if config.milvus.enabled and not args.allow_enabled:
        print(
            "경고: milvus.enabled=true 인 상태입니다. "
            "마이그레이션은 enabled=false 에서 돌리는 것을 권장합니다.\n"
            "강제하려면 --allow-enabled 를 붙이세요.",
            file=sys.stderr,
        )
        return 2

    db = await get_db_manager()
    store = milvus_store_mod.get_milvus_store()

    print(
        f"Milvus target: {config.milvus.host}:{config.milvus.port} "
        f"(db={config.milvus.db_name}, enabled={config.milvus.enabled})"
    )

    if args.verify_only:
        failures = await verify(db, store)
        return 1 if failures else 0

    faq_n = await migrate_faq(db, store, dry_run=args.dry_run)
    chunk_counts = await migrate_document_chunks(db, store, dry_run=args.dry_run)

    print("--- summary ---")
    print(f"faq migrated (or counted): {faq_n}")
    for size, n in chunk_counts.items():
        print(f"chunks_{size}: {n}")

    if args.dry_run:
        print("dry-run 완료. PG는 변경하지 않았습니다.")
        return 0

    if args.verify:
        failures = await verify(db, store)
        if failures:
            print(f"검증 실패 {failures}건. milvus.enabled 를 켜지 마세요.")
            return 1
        print("검증 통과. configs/config.yaml 에서 milvus.enabled: true 로 컷오버하세요.")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="PG 건수만 세고 Milvus에 쓰지 않습니다.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="마이그레이션 후 건수·샘플 자기-검색을 검증합니다.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="복사 없이 검증만 수행합니다.",
    )
    parser.add_argument(
        "--allow-enabled",
        action="store_true",
        help="milvus.enabled=true 여도 진행합니다.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
