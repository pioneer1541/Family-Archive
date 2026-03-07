from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings

settings = get_settings()

connect_args = {}
engine_kwargs = {"future": True}
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
elif settings.database_url.startswith("postgresql"):
    engine_kwargs["pool_pre_ping"] = settings.pg_pool_pre_ping
    engine_kwargs["pool_recycle"] = settings.pg_pool_recycle

engine = create_engine(
    settings.database_url, connect_args=connect_args, **engine_kwargs
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)


if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_on_connect(
        dbapi_connection, _connection_record
    ):  # pragma: no cover - driver callback
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
            cursor.execute(
                f"PRAGMA busy_timeout={max(0, int(settings.sqlite_busy_timeout_ms or 0))}"
            )
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
        tables = {
            str(row[0] or "")
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        if "documents" not in tables:
            return
        cols = {
            str(row[1] or "")
            for row in conn.execute(text("PRAGMA table_info(documents)"))
        }
        if "phash" not in cols:
            conn.execute(text("ALTER TABLE documents ADD COLUMN phash VARCHAR(32)"))
        if "summary_quality_state" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN summary_quality_state VARCHAR(24) DEFAULT 'unknown'"
                )
            )
        if "summary_last_error" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN summary_last_error VARCHAR(240) DEFAULT ''"
                )
            )
        if "summary_model" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN summary_model VARCHAR(64) DEFAULT ''"
                )
            )
        if "summary_version" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN summary_version VARCHAR(32) DEFAULT 'prompt-v2'"
                )
            )
        if "category_version" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN category_version VARCHAR(32) DEFAULT 'taxonomy-v1'"
                )
            )
        if "name_version" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN name_version VARCHAR(32) DEFAULT 'name-v2'"
                )
            )
        if "source_available_cached" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN source_available_cached BOOLEAN DEFAULT 1"
                )
            )
        if "source_checked_at" not in cols:
            conn.execute(
                text("ALTER TABLE documents ADD COLUMN source_checked_at DATETIME")
            )
        try:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_documents_phash ON documents(phash)"
                )
            )
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
            mail_cols = {
                str(row[1] or "")
                for row in conn.execute(
                    text("PRAGMA table_info(mail_ingestion_events)")
                )
            }
            if "sync_run_id" not in mail_cols:
                conn.execute(
                    text(
                        "ALTER TABLE mail_ingestion_events ADD COLUMN sync_run_id VARCHAR(36)"
                    )
                )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_mail_ingestion_events_sync_run_id ON mail_ingestion_events(sync_run_id)"
                )
            )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
