#!/usr/bin/env python3
"""계명대 FAQ mock 시드 → Postgres + 임베딩(Milvus dual-write).

`resources/faq_mock/kmu_faq_seed.json` 을 읽어
1) faq_categories / faq_items 에 PUBLISHED 로 넣고
2) faq_service.sync_faq_item 으로 임베딩한 뒤
3) (milvus.enabled=true 이면) Milvus FAQ 컬렉션에도 dual-write 합니다.
4) 샘플 질문으로 search_faq 스모크를 돌립니다.

API 컨테이너 예시:

    docker exec kmu-ai-api python -m scripts.seed_faq_mock
    docker exec kmu-ai-api python -m scripts.seed_faq_mock --smoke-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from app.config import config
from app.models.database import database_proxy
from app.models.enum import FaqStatus, FaqVisibility, Language, VectorStatus
from app.scheduler.jobs.chatbot import SYSTEM_USER_INFO
from app.utils.database import get_database, get_db_manager
from app.utils.faq_service import (
    EMBEDDING_VERSION,
    build_embedding_text,
    compute_text_hash,
    embed_texts,
    ensure_faq_collection,
    get_embedding_model,
    search_faq,
)
from app.utils import milvus_store as milvus_store_mod
import app.models.database as db_models
import app.models.db_item as db_items
import app.utils.common as util


DEFAULT_SEED = Path("resources/faq_mock/kmu_faq_seed.json")
MOCK_TAG = "mock_seed"
UPSERT_TIMEOUT = 120.0


def _enum_val(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def load_seed(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise SystemExit(f"시드 파일이 비어 있거나 배열이 아닙니다: {path}")
    return data


async def ensure_category(db, item: dict, order: int) -> db_items.FaqCategory:
    code = item["category_code"]
    query = db_models.FaqCategory.select().where(db_models.FaqCategory.category_code == code)
    existing: db_items.FaqCategory | None = await db.select_item(query)
    if existing:
        return existing

    cat_id = uuid4()
    await db.execute_query(db_models.FaqCategory.insert(
        id=cat_id,
        category_name=item["category_name"],
        category_code=code,
        department_code=item.get("department_code"),
        display_order=order,
        is_active=True,
        created_at=util.get_now(),
        updated_at=util.get_now(),
    ))
    print(f"  + category {code} ({item['category_name']})")
    return await db.select_item(
        db_models.FaqCategory.select().where(db_models.FaqCategory.id == cat_id)
    )


async def find_faq_by_question(db, question: str) -> db_items.FaqItem | None:
    query = (db_models.FaqItem.select()
             .where(db_models.FaqItem.question == question)
             .limit(1))
    return await db.select_item(query)


async def upsert_faq(db, category: db_items.FaqCategory, item: dict, *, force_update: bool) -> tuple[db_items.FaqItem, str]:
    """Returns (faq, action) where action is created|skipped|updated."""

    existing = await find_faq_by_question(db, item["question"])
    tags = list(item.get("tags") or [])
    if MOCK_TAG not in tags:
        tags.append(MOCK_TAG)

    if existing and not force_update:
        return existing, "skipped"

    if existing:
        await db.execute_query(
            db_models.FaqItem.update(
                category_id=category.id,
                answer=item["answer"],
                question_aliases_json=item.get("question_aliases") or [],
                tags_json=tags,
                source_url=item.get("source_url"),
                department_code=item.get("department_code"),
                visibility=FaqVisibility.PUBLIC.value,
                status=FaqStatus.PUBLISHED.value,
                language=Language.KO.value,
                updated_at=util.get_now(),
            ).where(db_models.FaqItem.id == existing.id),
            timeout=UPSERT_TIMEOUT,
        )
        faq = await db.select_item(
            db_models.FaqItem.select().where(db_models.FaqItem.id == existing.id)
        )
        return faq, "updated"

    faq_id = uuid4()
    await db.execute_query(
        db_models.FaqItem.insert(
            id=faq_id,
            category_id=category.id,
            question=item["question"],
            answer=item["answer"],
            question_aliases_json=item.get("question_aliases") or [],
            tags_json=tags,
            source_url=item.get("source_url"),
            department_code=item.get("department_code"),
            visibility=FaqVisibility.PUBLIC.value,
            status=FaqStatus.PUBLISHED.value,
            language=Language.KO.value,
            version=1,
            created_at=util.get_now(),
            updated_at=util.get_now(),
        ),
        timeout=UPSERT_TIMEOUT,
    )
    faq = await db.select_item(
        db_models.FaqItem.select().where(db_models.FaqItem.id == faq_id)
    )
    return faq, "created"


async def index_faq(
    db,
    collection: db_items.Collection,
    model: db_items.Model,
    faq: db_items.FaqItem,
) -> bool:
    """임베딩 + PG upsert(+ Milvus dual-write). 시드용으로 타임아웃을 넉넉히 잡는다."""

    query = (db_models.FaqCategory.select()
             .where(db_models.FaqCategory.id == faq.category_id))
    category: db_items.FaqCategory | None = await db.select_item(query)

    embedding_text = build_embedding_text(faq.question, faq.question_aliases_json)
    text_hash = compute_text_hash(embedding_text)
    now = util.get_now()

    try:
        vectors = await embed_texts(model, SYSTEM_USER_INFO, [embedding_text])
        vector = vectors[0]
        vector_status = VectorStatus.INDEXED.value
    except Exception as exc:
        print(f"  embed error: {exc}")
        vector = None
        vector_status = VectorStatus.FAILED.value

    fields = {
        "embedding_text": embedding_text,
        "embedding_text_hash": text_hash,
        "embedding_model": collection.embedding_model,
        "embedding_version": EMBEDDING_VERSION,
        "category_code": category.category_code if category else None,
        "department_code": faq.department_code,
        "language": _enum_val(faq.language),
        "visibility": _enum_val(faq.visibility),
        "status": _enum_val(faq.status),
        "tags": faq.tags_json or [],
        "source_url": faq.source_url,
        "question": faq.question,
        "version": faq.version,
        "updated_at": now,
        "embedding": vector,
        "vector_status": vector_status,
        "indexed_at": now if vector is not None else None,
    }

    existing = await db.select_item(
        db_models.FaqEmbedding.select().where(db_models.FaqEmbedding.faq_id == faq.id)
    )
    if existing:
        await db.execute_query(
            db_models.FaqEmbedding.update(**fields).where(db_models.FaqEmbedding.id == existing.id),
            timeout=UPSERT_TIMEOUT,
        )
    else:
        await db.execute_query(
            db_models.FaqEmbedding.insert(faq_id=faq.id, **fields),
            timeout=UPSERT_TIMEOUT,
        )

    if milvus_store_mod.is_milvus_enabled():
        try:
            entity = milvus_store_mod.faq_fields_to_entity(
                faq_id=faq.id, embedding=vector, fields=fields,
            )
            if entity is None:
                await milvus_store_mod.delete_faq_async(faq.id)
            else:
                await milvus_store_mod.upsert_faq_async(db_models.FAQ_EMBEDDING_DIM, entity)
        except Exception as exc:
            # PG에는 들어갔으니 시드를 중단하지 않는다. 나중에 migrate/재시드로 Milvus를 맞출 수 있다.
            print(f"    milvus upsert warn: {exc}")

    return vector is not None


async def sync_all(db, faqs: list[db_items.FaqItem]) -> tuple[int, int]:
    collection, err = await ensure_faq_collection(
        db, config.chatbot.embedding_model, SYSTEM_USER_INFO,
    )
    if err or not collection:
        raise SystemExit(f"FAQ 컬렉션 준비 실패: {err}")

    model = await get_embedding_model(db, collection)
    if not model:
        raise SystemExit("임베딩 모델을 사용할 수 없습니다. (kmu-embedding RUNNING 여부 확인)")

    ok = fail = 0
    for i, faq in enumerate(faqs, 1):
        print(f"  [{i}/{len(faqs)}] embedding: {faq.question[:40]}...")
        if await index_faq(db, collection, model, faq):
            ok += 1
            print("    → indexed")
        else:
            fail += 1
            print("    → failed")
    return ok, fail


async def smoke_search(db, queries: list[str]) -> None:
    print(f"\n[smoke] milvus.enabled={milvus_store_mod.is_milvus_enabled()}")
    for q in queries:
        results, ms = await search_faq(
            db, SYSTEM_USER_INFO, q, top_k=3, score_threshold=0.2, with_answer=True,
        )
        print(f"\nQ: {q}  ({ms}ms, hits={len(results)})")
        for i, r in enumerate(results, 1):
            ans = (r.answer or "")[:80].replace("\n", " ")
            print(f"  {i}. score={r.score:.3f} | {r.question}")
            print(f"     → {ans}...")


async def main_async(args: argparse.Namespace) -> int:
    seed_path = Path(args.seed)
    items = load_seed(seed_path)

    # FastAPI lifespan 밖에서 돌리므로 DatabaseProxy를 직접 바인딩한다.
    database_proxy.initialize(get_database())
    db = await get_db_manager()

    if args.smoke_only:
        await smoke_search(db, args.query or [
            "수강신청 언제 해?",
            "휴학하려면 어디로 가?",
            "장학금 신청하고 싶어요",
        ])
        return 0

    print(f"시드 {len(items)}건 로드: {seed_path}")
    print(f"embedding_model={config.chatbot.embedding_model}, milvus.enabled={config.milvus.enabled}")

    # 카테고리
    codes_seen: dict[str, db_items.FaqCategory] = {}
    order = 0
    for item in items:
        code = item["category_code"]
        if code not in codes_seen:
            order += 10
            codes_seen[code] = await ensure_category(db, item, order)

    # FAQ
    faqs: list[db_items.FaqItem] = []
    created = updated = skipped = 0
    for item in items:
        faq, action = await upsert_faq(
            db, codes_seen[item["category_code"]], item, force_update=args.force_update,
        )
        faqs.append(faq)
        if action == "created":
            created += 1
            print(f"  + create {item['question'][:50]}")
        elif action == "updated":
            updated += 1
            print(f"  ~ update {item['question'][:50]}")
        else:
            skipped += 1
            print(f"  = skip   {item['question'][:50]}")

    print(f"\nFAQ created={created}, updated={updated}, skipped={skipped}. 임베딩 시작...")
    ok, fail = await sync_all(db, faqs)
    print(f"indexed={ok}, failed={fail}, milvus.enabled={config.milvus.enabled}")

    if milvus_store_mod.is_milvus_enabled():
        count = milvus_store_mod.get_milvus_store().count_faq()
        print(f"Milvus faq_embeddings entities≈{count}")

    if not args.no_smoke:
        await smoke_search(db, args.query or [
            "수강신청 기간 알려줘",
            "기숙사 언제 신청해?",
            "증명서 어디서 받아?",
        ])

    return 1 if fail else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed", default=str(DEFAULT_SEED), help="시드 JSON 경로")
    p.add_argument("--smoke-only", action="store_true", help="시드 없이 검색만")
    p.add_argument("--no-smoke", action="store_true", help="시드 후 스모크 생략")
    p.add_argument("--force-update", action="store_true", help="기존 FAQ 원문도 덮어씁니다")
    p.add_argument("--query", action="append", help="스모크 질의 (여러 번 지정 가능)")
    return p


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
