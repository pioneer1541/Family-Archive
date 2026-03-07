import argparse
import json
import os
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import MetaData, Table, create_engine, func, select
from sqlalchemy.engine import Engine
def _sqlite_path_from_url(database_url: str) -> str:
    raw = str(database_url or "").strip()
    if raw.startswith("sqlite:////"):
        return "/" + raw[len("sqlite:////") :]
    if raw.startswith("sqlite:///"):
        return raw[len("sqlite:///") :]
    return ""


def _resolve_sqlite_path(cli_path: str | None) -> str:
    path = str(cli_path or "").strip()
    if path:
        return path
    for key in ("SQLITE_PATH", "FAMILY_VAULT_SQLITE_PATH"):
        value = str(os.getenv(key) or "").strip()
        if value:
            return value
    fv_url = str(os.getenv("FAMILY_VAULT_DATABASE_URL") or "").strip()
    guessed = _sqlite_path_from_url(fv_url)
    if guessed:
        return guessed
    return str((Path(__file__).resolve().parents[2] / "data" / "family_vault.db").resolve())


def _resolve_pg_url(cli_url: str | None) -> str:
    value = str(cli_url or "").strip()
    if value:
        return value
    return str(os.getenv("DATABASE_URL") or "").strip()


def _connect_engines(sqlite_path: str, pg_url: str) -> tuple[Engine, Engine]:
    sqlite_url = f"sqlite:///{sqlite_path}"
    sqlite_engine = create_engine(sqlite_url, future=True)
    pg_engine = create_engine(pg_url, future=True)
    return sqlite_engine, pg_engine


def _table_order(engine: Engine, include_tables: set[str] | None = None) -> list[str]:
    inspector = sa.inspect(engine)
    names = [n for n in inspector.get_table_names() if n != "sqlite_sequence"]
    if include_tables:
        names = [n for n in names if n in include_tables]

    deps: dict[str, set[str]] = {name: set() for name in names}
    dependents: dict[str, set[str]] = defaultdict(set)
    for table in names:
        for fk in inspector.get_foreign_keys(table):
            referred = str((fk or {}).get("referred_table") or "").strip()
            if referred and referred in deps and referred != table:
                deps[table].add(referred)
                dependents[referred].add(table)

    q = deque(sorted([t for t, required in deps.items() if not required]))
    ordered: list[str] = []
    while q:
        current = q.popleft()
        ordered.append(current)
        for dep in sorted(dependents.get(current, set())):
            deps[dep].discard(current)
            if not deps[dep]:
                q.append(dep)

    if len(ordered) == len(names):
        return ordered
    remaining = sorted([t for t in names if t not in set(ordered)])
    return ordered + remaining


def _quote_table_name(table_name: str) -> str:
    return '"' + str(table_name).replace('"', '""') + '"'


def _count_rows(conn: sa.Connection, table: Table) -> int:
    return int(conn.execute(select(func.count()).select_from(table)).scalar_one() or 0)


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if value is None:
            out[key] = None
        elif isinstance(value, (str, int, float, bool)):
            out[key] = value
        else:
            out[key] = str(value)
    return out


def _sample_rows(
    conn: sa.Connection, table: Table, pk_cols: list[str], sample_size: int
) -> list[dict[str, Any]]:
    stmt = select(table)
    for col in pk_cols:
        stmt = stmt.order_by(table.c[col])
    stmt = stmt.limit(max(0, int(sample_size)))
    rows = conn.execute(stmt).mappings().all()
    return [_normalize_row(dict(row)) for row in rows]


def migrate_sqlite_to_pg(
    *,
    sqlite_path: str,
    pg_url: str,
    dry_run: bool,
    batch_size: int,
    sample_size: int,
    truncate_first: bool,
    include_tables: set[str] | None = None,
) -> dict[str, Any]:
    sqlite_engine, pg_engine = _connect_engines(sqlite_path, pg_url)
    sqlite_meta = MetaData()
    pg_meta = MetaData()
    sqlite_meta.reflect(bind=sqlite_engine)
    pg_meta.reflect(bind=pg_engine)

    table_names = _table_order(sqlite_engine, include_tables=include_tables)
    per_table: list[dict[str, Any]] = []

    with sqlite_engine.connect() as sqlite_conn:
        if dry_run:
            with pg_engine.connect() as pg_conn:
                for table_name in table_names:
                    sqlite_table = sqlite_meta.tables.get(table_name)
                    pg_table = pg_meta.tables.get(table_name)
                    if sqlite_table is None or pg_table is None:
                        per_table.append(
                            {
                                "table": table_name,
                                "status": "skipped_missing_table",
                            }
                        )
                        continue
                    src_count = _count_rows(sqlite_conn, sqlite_table)
                    dst_count = _count_rows(pg_conn, pg_table)
                    per_table.append(
                        {
                            "table": table_name,
                            "source_rows": src_count,
                            "target_rows": dst_count,
                            "status": "planned",
                        }
                    )
        else:
            with pg_engine.begin() as pg_conn:
                for table_name in table_names:
                    sqlite_table = sqlite_meta.tables.get(table_name)
                    pg_table = pg_meta.tables.get(table_name)
                    if sqlite_table is None or pg_table is None:
                        per_table.append(
                            {
                                "table": table_name,
                                "status": "skipped_missing_table",
                            }
                        )
                        continue

                    src_count = _count_rows(sqlite_conn, sqlite_table)
                    if truncate_first:
                        pg_conn.execute(
                            sa.text(
                                f"TRUNCATE TABLE {_quote_table_name(table_name)} RESTART IDENTITY CASCADE"
                            )
                        )

                    inserted = 0
                    stmt = select(sqlite_table)
                    rows = sqlite_conn.execute(stmt).mappings()
                    buffer: list[dict[str, Any]] = []
                    for row in rows:
                        payload = {
                            col_name: row.get(col_name)
                            for col_name in pg_table.columns.keys()
                        }
                        buffer.append(payload)
                        if len(buffer) >= max(1, int(batch_size)):
                            pg_conn.execute(pg_table.insert(), buffer)
                            inserted += len(buffer)
                            buffer = []
                    if buffer:
                        pg_conn.execute(pg_table.insert(), buffer)
                        inserted += len(buffer)
                    per_table.append(
                        {
                            "table": table_name,
                            "source_rows": src_count,
                            "inserted_rows": inserted,
                            "status": "imported",
                        }
                    )

    validation: list[dict[str, Any]] = []
    with sqlite_engine.connect() as sqlite_conn, pg_engine.connect() as pg_conn:
        inspector = sa.inspect(sqlite_engine)
        for table_name in table_names:
            sqlite_table = sqlite_meta.tables.get(table_name)
            pg_table = pg_meta.tables.get(table_name)
            if sqlite_table is None or pg_table is None:
                continue
            src_count = _count_rows(sqlite_conn, sqlite_table)
            dst_count = _count_rows(pg_conn, pg_table)
            pk_cols = list((inspector.get_pk_constraint(table_name) or {}).get("constrained_columns") or [])
            sample_match: bool | None = None
            if (not dry_run) and pk_cols and sample_size > 0:
                src_sample = _sample_rows(sqlite_conn, sqlite_table, pk_cols, sample_size)
                dst_sample = _sample_rows(pg_conn, pg_table, pk_cols, sample_size)
                sample_match = src_sample == dst_sample
            validation.append(
                {
                    "table": table_name,
                    "source_rows": src_count,
                    "target_rows": dst_count,
                    "row_count_match": src_count == dst_count,
                    "sample_match": sample_match,
                }
            )

    ok = all(bool(v.get("row_count_match")) for v in validation)
    if not dry_run:
        for item in validation:
            sample_match = item.get("sample_match")
            if sample_match is False:
                ok = False

    return {
        "sqlite_path": sqlite_path,
        "pg_url_set": bool(pg_url),
        "dry_run": dry_run,
        "batch_size": int(batch_size),
        "sample_size": int(sample_size),
        "truncate_first": bool(truncate_first),
        "tables": table_names,
        "results": per_table,
        "validation": validation,
        "ok": ok,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate SQLite data into PostgreSQL.")
    parser.add_argument("--sqlite-path", default="", help="SQLite DB file path.")
    parser.add_argument("--pg-url", default="", help="PostgreSQL URL. Defaults to DATABASE_URL.")
    parser.add_argument("--batch-size", type=int, default=1000, help="Insert batch size.")
    parser.add_argument("--sample-size", type=int, default=10, help="Validation sample size per table.")
    parser.add_argument("--tables", default="", help="Comma-separated table whitelist.")
    parser.add_argument("--truncate-first", action="store_true", help="Truncate target tables before import.")
    parser.add_argument("--dry-run", action="store_true", help="Plan and validate only, no writes.")
    parser.add_argument("--output", default="", help="Optional output JSON file path.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    sqlite_path = _resolve_sqlite_path(str(args.sqlite_path))
    pg_url = _resolve_pg_url(str(args.pg_url))
    if not sqlite_path:
        raise SystemExit("sqlite_path_required")
    if not pg_url:
        raise SystemExit("pg_url_required: provide --pg-url or DATABASE_URL")

    include_tables = {
        t.strip() for t in str(args.tables or "").split(",") if t.strip()
    } or None
    report = migrate_sqlite_to_pg(
        sqlite_path=sqlite_path,
        pg_url=pg_url,
        dry_run=bool(args.dry_run),
        batch_size=max(1, int(args.batch_size)),
        sample_size=max(0, int(args.sample_size)),
        truncate_first=bool(args.truncate_first),
        include_tables=include_tables,
    )

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if str(args.output or "").strip():
        output_path = Path(str(args.output)).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
