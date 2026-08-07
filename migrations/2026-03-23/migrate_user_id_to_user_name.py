#!/usr/bin/env python3
"""기존 DB의 user_id(Keycloak sub) 값을 user_name(preferred_username)으로 치환한 뒤 컬럼명을 변경합니다.

배포 순서: 이 스크립트로 DB를 먼저 갱신한 뒤, user_name 컬럼을 사용하는 애플리케이션 버전을 배포합니다.
(기존 코드는 user_id 컬럼을 참조하므로, 마이그레이션 직후·신규 코드 배포 전에는 짧은 중단이 필요할 수 있습니다.)

  cd <프로젝트 루트>
  python migrations/2026-03-23/migrate_user_id_to_user_name.py
  python migrations/2026-03-23/migrate_user_id_to_user_name.py --dry-run

환경: configs/config.yaml 의 database·auth 설정과 동일해야 합니다.
"""

from __future__ import annotations

import argparse
import sys

from pathlib import Path

# 프로젝트 루트를 import 경로에 추가
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import psycopg2
from keycloak import KeycloakAdmin
from keycloak.exceptions import KeycloakGetError

from app.config import config


def _column_names(cur, table: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    return {r[0] for r in cur.fetchall()}


def _build_admins() -> list[KeycloakAdmin]:
    admins: list[KeycloakAdmin] = []
    for auth in config.auth.servers:
        admins.append(
            KeycloakAdmin(
                server_url=auth.server_url,
                realm_name=auth.realm_name,
                verify=True,
                username=auth.admin_username,
                password=auth.admin_password,
            )
        )
    return admins


def _sub_to_username(admins: list[KeycloakAdmin], sub: str) -> str:
    if sub.startswith("whitelist:"):
        return sub
    for admin in admins:
        try:
            user = admin.get_user(sub)
            if user and user.get("username"):
                return user["username"]
        except KeycloakGetError:
            continue
        except Exception:
            continue
    return sub


def _distinct_values(cur, sql: str) -> list[str]:
    cur.execute(sql)
    return [r[0] for r in cur.fetchall() if r[0] is not None]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Keycloak 조회와 매핑만 수행하고 DB는 변경하지 않습니다.",
    )
    args = parser.parse_args()

    admins = _build_admins()

    conn = psycopg2.connect(
        host=config.database.host,
        port=config.database.port,
        user=config.database.username,
        password=config.database.password,
        database=config.database.name,
    )
    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            mu_cols = _column_names(cur, "model_usages")
            col_cols = _column_names(cur, "collections")
            doc_cols = _column_names(cur, "documents")

            if "user_name" in mu_cols and "user_id" not in mu_cols:
                print("model_usages: 이미 user_name 컬럼만 있습니다. user_id 값 마이그레이션은 건너뜁니다.")
            elif "user_id" in mu_cols:
                subs: set[str] = set()
                subs |= set(_distinct_values(cur, "SELECT DISTINCT user_id FROM model_usages"))
                subs |= set(_distinct_values(cur, "SELECT DISTINCT user_id FROM collections"))
                subs |= set(_distinct_values(cur, "SELECT DISTINCT user_id FROM documents"))
                subs |= set(_distinct_values(cur, "SELECT DISTINCT created_by FROM prompts"))
                subs |= set(_distinct_values(cur, "SELECT DISTINCT created_by FROM prompt_versions"))

                mapping: dict[str, str] = {}
                for sub in sorted(subs):
                    un = _sub_to_username(admins, sub)
                    mapping[sub] = un
                    if un != sub:
                        print(f"  매핑: {sub!r} -> {un!r}")
                    else:
                        print(f"  유지: {sub!r}")

                if args.dry_run:
                    print("--dry-run: DB UPDATE/RENAME 을 수행하지 않습니다.")
                    conn.rollback()
                    return 0

                for table, col in (
                    ("model_usages", "user_id"),
                    ("collections", "user_id"),
                    ("documents", "user_id"),
                ):
                    for old, new in mapping.items():
                        if old == new:
                            continue
                        cur.execute(
                            f'UPDATE {table} SET "{col}" = %s WHERE "{col}" = %s',
                            (new, old),
                        )
                        if cur.rowcount:
                            print(f"  {table}.{col}: {cur.rowcount} 행 갱신 ({old!r} -> {new!r})")

                for table in ("prompts", "prompt_versions"):
                    for old, new in mapping.items():
                        if old == new:
                            continue
                        cur.execute(
                            f'UPDATE {table} SET created_by = %s WHERE created_by = %s',
                            (new, old),
                        )
                        if cur.rowcount:
                            print(f"  {table}.created_by: {cur.rowcount} 행 갱신 ({old!r} -> {new!r})")

                for table in ("model_usages", "collections", "documents"):
                    cur.execute(
                        f'ALTER TABLE {table} RENAME COLUMN user_id TO user_name'
                    )
                    print(f"  {table}: 컬럼 user_id -> user_name 으로 이름 변경")

            else:
                print(
                    "model_usages에 user_id 도 user_name 도 없습니다. 스키마를 확인하세요.",
                    file=sys.stderr,
                )
                conn.rollback()
                return 1

        conn.commit()
        print("마이그레이션 완료.")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
