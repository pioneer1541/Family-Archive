from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings
from app.logging_utils import get_logger, sanitize_log_context

settings = get_settings()
logger = get_logger(__name__)

connect_args = {}
engine_kwargs = {"future": True}
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
elif settings.database_url.startswith("postgresql"):
    engine_kwargs["pool_pre_ping"] = settings.pg_pool_pre_ping
    engine_kwargs["pool_recycle"] = settings.pg_pool_recycle
    engine_kwargs["pool_size"] = settings.pg_pool_size
    engine_kwargs["max_overflow"] = settings.pg_max_overflow

engine = create_engine(settings.database_url, connect_args=connect_args, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)


if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_on_connect(dbapi_connection, _connection_record):  # pragma: no cover - driver callback
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        try:
            cursor.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        try:
            cursor.execute(f"PRAGMA busy_timeout={max(0, int(settings.sqlite_busy_timeout_ms or 0))}")
        except Exception:
            pass
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
        cursor.close()


class Base(DeclarativeBase):
    pass


def ensure_sqlite_runtime_schema() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.begin() as conn:
        def _sqlite_add_column_if_missing(table: str, cols: set[str], column: str, ddl: str) -> None:
            if column in cols:
                return
            try:
                conn.execute(text(ddl))
            except Exception as exc:
                logger.warning(
                    "sqlite_alter_table_failed",
                    extra=sanitize_log_context(
                        {
                            "status": "warn",
                            "error_code": "sqlite_alter_table_failed",
                            "table": table,
                            "column": column,
                            "detail": str(exc),
                        }
                    ),
                )

        tables = {str(row[0] or "") for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "documents" not in tables:
            return
        cols = {str(row[1] or "") for row in conn.execute(text("PRAGMA table_info(documents)"))}
        _sqlite_add_column_if_missing("documents", cols, "phash", "ALTER TABLE documents ADD COLUMN phash VARCHAR(32)")
        _sqlite_add_column_if_missing(
            "documents",
            cols,
            "summary_quality_state",
            "ALTER TABLE documents ADD COLUMN summary_quality_state VARCHAR(24) DEFAULT 'unknown'",
        )
        _sqlite_add_column_if_missing(
            "documents",
            cols,
            "summary_last_error",
            "ALTER TABLE documents ADD COLUMN summary_last_error VARCHAR(240) DEFAULT ''",
        )
        _sqlite_add_column_if_missing(
            "documents",
            cols,
            "summary_model",
            "ALTER TABLE documents ADD COLUMN summary_model VARCHAR(64) DEFAULT ''",
        )
        _sqlite_add_column_if_missing(
            "documents",
            cols,
            "summary_version",
            "ALTER TABLE documents ADD COLUMN summary_version VARCHAR(32) DEFAULT 'prompt-v2'",
        )
        _sqlite_add_column_if_missing(
            "documents",
            cols,
            "category_version",
            "ALTER TABLE documents ADD COLUMN category_version VARCHAR(32) DEFAULT 'taxonomy-v1'",
        )
        _sqlite_add_column_if_missing(
            "documents",
            cols,
            "name_version",
            "ALTER TABLE documents ADD COLUMN name_version VARCHAR(32) DEFAULT 'name-v2'",
        )
        _sqlite_add_column_if_missing(
            "documents",
            cols,
            "source_available_cached",
            "ALTER TABLE documents ADD COLUMN source_available_cached BOOLEAN DEFAULT 1",
        )
        _sqlite_add_column_if_missing(
            "documents",
            cols,
            "source_checked_at",
            "ALTER TABLE documents ADD COLUMN source_checked_at DATETIME",
        )
        try:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_documents_phash ON documents(phash)"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_documents_status_src_cached ON documents(status, source_available_cached)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_documents_category_src_cached ON documents(category_path, source_available_cached)"
                )
            )
        except Exception:
            pass  # 索引为纯性能优化，创建失败不中断 lifespan（测试环境 DB 状态可能短暂不稳定）
        if "mail_ingestion_events" in tables:
            mail_cols = {str(row[1] or "") for row in conn.execute(text("PRAGMA table_info(mail_ingestion_events)"))}
            _sqlite_add_column_if_missing(
                "mail_ingestion_events",
                mail_cols,
                "sync_run_id",
                "ALTER TABLE mail_ingestion_events ADD COLUMN sync_run_id VARCHAR(36)",
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_mail_ingestion_events_sync_run_id ON mail_ingestion_events(sync_run_id)"
                )
            )
        if "gmail_credentials" in tables:
            gmail_cols = {str(row[1] or "") for row in conn.execute(text("PRAGMA table_info(gmail_credentials)"))}
            _sqlite_add_column_if_missing(
                "gmail_credentials",
                gmail_cols,
                "token_expiry",
                "ALTER TABLE gmail_credentials ADD COLUMN token_expiry DATETIME",
            )
        if "users" in tables:
            user_cols = {str(row[1] or "") for row in conn.execute(text("PRAGMA table_info(users)"))}
            username_missing = "username" not in user_cols
            if username_missing:
                try:
                    conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR(64)"))
                except Exception as exc:
                    # If another process already added the column, continue with backfill/index.
                    if "duplicate column name" not in str(exc).lower():
                        raise
            try:
                existing_usernames = {
                    str(row["username"]).strip()
                    for row in conn.execute(text("SELECT username FROM users WHERE username IS NOT NULL")).mappings()
                    if str(row["username"]).strip()
                }
                user_rows = conn.execute(
                    text("SELECT id, email FROM users WHERE username IS NULL ORDER BY id")
                ).mappings().all()
                for row in user_rows:
                    user_id = row["id"]
                    email = str(row.get("email") or "").strip().lower()
                    if email == "admin@local":
                        base_username = "admin"
                    else:
                        base_username = (email.split("@", 1)[0].strip().lower() if email else "") or "user"
                    candidate = base_username[:64] or "user"
                    suffix = 2
                    while candidate in existing_usernames:
                        suffix_str = f"_{suffix}"
                        candidate = f"{base_username[: max(1, 64 - len(suffix_str))]}{suffix_str}"
                        suffix += 1
                    existing_usernames.add(candidate)
                    conn.execute(
                        text("UPDATE users SET username = :username WHERE id = :user_id"),
                        {"username": candidate, "user_id": user_id},
                    )
            except Exception as exc:
                logger.warning(
                    "sqlite_users_username_backfill_failed",
                    extra=sanitize_log_context(
                        {
                            "status": "warn",
                            "error_code": "sqlite_users_username_backfill_failed",
                            "table": "users",
                            "column": "username",
                            "detail": str(exc),
                        }
                    ),
                )
            try:
                usernames_in_use = {
                    str(row["username"]).strip()
                    for row in conn.execute(text("SELECT username FROM users WHERE username IS NOT NULL")).mappings()
                    if str(row["username"]).strip()
                }
                duplicate_usernames = conn.execute(
                    text(
                        """
                        SELECT username
                        FROM users
                        WHERE username IS NOT NULL
                        GROUP BY username
                        HAVING COUNT(*) > 1
                        """
                    )
                ).mappings().all()
                for dup in duplicate_usernames:
                    username = dup["username"]
                    dup_rows = conn.execute(
                        text("SELECT id FROM users WHERE username = :username ORDER BY id"),
                        {"username": username},
                    ).mappings().all()
                    if len(dup_rows) <= 1:
                        continue
                    base_username = (str(username or "").strip()[:64] or "user")
                    usernames_in_use.discard(base_username)
                    suffix = 2
                    for row in dup_rows[1:]:
                        user_id = row["id"]
                        candidate = f"{base_username[: max(1, 64 - len(f'_{suffix}'))]}_{suffix}"
                        while candidate in usernames_in_use:
                            suffix += 1
                            candidate = f"{base_username[: max(1, 64 - len(f'_{suffix}'))]}_{suffix}"
                        conn.execute(
                            text("UPDATE users SET username = :username WHERE id = :user_id"),
                            {"username": candidate, "user_id": user_id},
                        )
                        usernames_in_use.add(candidate)
                        suffix += 1
                    usernames_in_use.add(base_username)
            except Exception as exc:
                logger.warning(
                    "sqlite_users_username_dedup_failed",
                    extra=sanitize_log_context(
                        {
                            "status": "warn",
                            "error_code": "sqlite_users_username_dedup_failed",
                            "table": "users",
                            "column": "username",
                            "detail": str(exc),
                        }
                    ),
                )
            try:
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users(username)"))
            except Exception as exc:
                logger.warning(
                    "sqlite_users_username_index_failed",
                    extra=sanitize_log_context(
                        {
                            "status": "warn",
                            "error_code": "sqlite_users_username_index_failed",
                            "table": "users",
                            "column": "username",
                            "detail": str(exc),
                        }
                    ),
                )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
