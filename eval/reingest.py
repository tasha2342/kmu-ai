"""규정 코퍼스 재색인 복구 스크립트.

2026-08-03 인제스트 잡이 `3-1-4위임전결규정`에서 프로세스와 함께 죽었고, 그 뒤
아무도 재시도하지 않아 **181문서 중 45개만 색인된 상태로 8일** 지났습니다.
이 스크립트는 프로덕션 코드 경로를 그대로 써서 나머지를 채웁니다.

직접 SQL로 청크를 밀어 넣지 않습니다. `run_regulation_ingestion()`을 부르는 이유는
청킹·임베딩·포인트 변환이 프로덕션과 한 글자도 다르면 안 되기 때문입니다.

- `force=False` — 이미 완료된 45개는 원문 해시가 같으므로 건너뜁니다.
  멈춘 문서(`status != completed`)는 건너뛰지 않고 다시 처리됩니다.
- 죽은 잡을 먼저 회수합니다. 안 그러면 중복 방지 가드에 걸립니다.

    docker exec kmu-ai-api python /app/resources/eval_live/reingest.py
"""

from __future__ import annotations

import asyncio
import sys
import time

if "/app" not in sys.path:
    sys.path.insert(0, "/app")


async def main() -> None:
    from app.models.auth import TokenUserInfo
    from app.models.enum import DocumentStatus, IngestionStatus, SourceType
    from app.scheduler.jobs.chatbot import (
        create_ingestion_job,
        get_running_job,
        reap_stale_jobs,
        run_regulation_ingestion,
    )
    from app.models.database import database_proxy
    from app.utils.database import get_database, get_db_manager

    db_manager = await get_db_manager()
    db = get_database()

    # 앱 기동(main.py lifespan)이 하는 일을 여기서도 해 줘야 합니다.
    # peewee 모델은 DatabaseProxy에 바인딩돼 있어, 초기화하지 않으면
    # insert/update가 "Cannot use uninitialized Proxy"로 죽습니다.
    # (select는 db_manager가 커넥션을 직접 넘겨 줘서 우연히 동작합니다)
    database_proxy.initialize(db)

    def count(sql: str) -> int:
        return db.execute_sql(sql).fetchone()[0]

    before_docs = count("SELECT count(*) FROM documents WHERE status='completed'")
    before_chunks = count(
        "SELECT count(*) FROM document_chunks_1024 WHERE collection_name='kmu_regulations'"
    )
    print(f"[시작] 완료 문서 {before_docs} · 청크 {before_chunks}", flush=True)

    # 1) 죽은 잡 회수. 이걸 안 하면 create가 중복 방지 가드에 막힙니다.
    reaped = await reap_stale_jobs(db_manager, SourceType.REGULATION)
    print(f"[회수] 응답 없는 잡 {reaped}건을 failed로 마감", flush=True)

    still = await get_running_job(db_manager, SourceType.REGULATION)
    if still:
        print(f"[중단] 아직 running인 잡이 있습니다: {still.id}", flush=True)
        return



    # 3) 프로덕션 경로로 재색인
    job_id = await create_ingestion_job(db_manager, SourceType.REGULATION)
    print(f"[실행] job_id={job_id} (force=False — 완료분은 건너뜁니다)", flush=True)

    started = time.time()
    total, success, failed, skipped = await run_regulation_ingestion(
        db_manager=db_manager,
        job_id=job_id,
        user_info=TokenUserInfo(sub="reingest-recovery", username="recovery", roles=["admin"]),
        force=False,
    )
    elapsed = time.time() - started

    after_docs = count("SELECT count(*) FROM documents WHERE status='completed'")
    after_chunks = count(
        "SELECT count(*) FROM document_chunks_1024 WHERE collection_name='kmu_regulations'"
    )
    print(
        f"\n[완료] {elapsed / 60:.1f}분\n"
        f"  전체 {total} · 성공 {success} · 실패 {failed} · 건너뜀 {skipped}\n"
        f"  문서 {before_docs} → {after_docs} · 청크 {before_chunks} → {after_chunks}",
        flush=True,
    )

    # 실패한 문서는 이름을 남깁니다. 조용히 넘어가면 다음에도 모릅니다.
    rows = db.execute_sql(
        "SELECT file_name, LEFT(COALESCE(error_message,''),120) "
        "FROM documents WHERE status NOT IN ('completed') ORDER BY file_name"
    ).fetchall()
    if rows:
        print(f"\n[미완료 {len(rows)}건]", flush=True)
        for name, err in rows:
            print(f"  {name}  {err}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
