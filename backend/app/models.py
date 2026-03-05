import datetime as dt
import enum
import uuid

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class DocumentStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DUPLICATE = "duplicate"


class IngestionJobStatus(str, enum.Enum):
    CREATED = "created"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatus(str, enum.Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REOPENED = "reopened"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_ext: Mapped[str] = mapped_column(String(16), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    phash: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=DocumentStatus.PENDING.value)
    duplicate_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)

    doc_lang: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    title_en: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    title_zh: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    summary_en: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary_zh: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category_label_en: Mapped[str] = mapped_column(String(128), nullable=False, default="Uncategorized")
    category_label_zh: Mapped[str] = mapped_column(String(128), nullable=False, default="未分类")
    category_path: Mapped[str] = mapped_column(String(256), nullable=False, default="archive/misc")
    summary_quality_state: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    summary_last_error: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    summary_model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    summary_version: Mapped[str] = mapped_column(String(32), nullable=False, default="prompt-v2")
    category_version: Mapped[str] = mapped_column(String(32), nullable=False, default="taxonomy-v1")
    name_version: Mapped[str] = mapped_column(String(32), nullable=False, default="name-v2")
    source_available_cached: Mapped[bool] = mapped_column(nullable=False, default=True, index=True)
    source_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # OCR truncation tracking (PDFs only)
    ocr_pages_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ocr_pages_processed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Long-document map-reduce sampling tracking
    longdoc_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    longdoc_pages_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    longdoc_pages_used: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Map-reduce checkpoint fields — persist intermediate results so that a
    # mid-flight timeout does not lose already-completed page/section summaries.
    mapreduce_page_summaries_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]", server_default="[]")
    mapreduce_section_summaries_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    mapreduce_job_status: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )

    chunks: Mapped[list["Chunk"]] = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")
    tags: Mapped[list["DocumentTag"]] = relationship(
        "DocumentTag", back_populates="document", cascade="all, delete-orphan"
    )
    bill_fact: Mapped["BillFact | None"] = relationship(
        "BillFact",
        back_populates="document",
        cascade="all, delete-orphan",
        uselist=False,
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id"), index=True, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )

    document: Mapped[Document] = relationship("Document", back_populates="chunks")


class BillFact(Base):
    __tablename__ = "bill_facts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id"), nullable=False, unique=True, index=True
    )
    vendor: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    amount_due: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(12), nullable=False, default="AUD")
    due_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    billing_period_start: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    billing_period_end: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    payment_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    extraction_version: Mapped[str] = mapped_column(String(32), nullable=False, default="bill-facts-v1")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )

    document: Mapped[Document] = relationship("Document", back_populates="bill_fact")


class DocumentTag(Base):
    __tablename__ = "document_tags"

    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id"),
        primary_key=True,
        nullable=False,
        index=True,
    )
    tag_key: Mapped[str] = mapped_column(String(128), primary_key=True, nullable=False)
    family: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    value: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )

    document: Mapped[Document] = relationship("Document", back_populates="tags")


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=IngestionJobStatus.CREATED.value)
    input_paths: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    nas_job_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    mail_job_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    nas_summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    mail_summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )

    items: Mapped[list["SyncRunItem"]] = relationship("SyncRunItem", back_populates="run", cascade="all, delete-orphan")


class SyncRunItem(Base):
    __tablename__ = "sync_run_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("sync_runs.id"), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False, default="nas")
    source_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    file_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    doc_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(24), nullable=False, default="discovered")
    detail: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )

    run: Mapped["SyncRun"] = relationship("SyncRun", back_populates="items")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    doc_set: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    filters: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    summary_en: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary_zh: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TaskStatus.CREATED.value)
    created_time: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )
    updated_time: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )


class SourceFileState(Base):
    __tablename__ = "source_file_states"

    path: Mapped[str] = mapped_column(String(1024), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="nas")
    mtime_ns: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )


class MailProcessedMessage(Base):
    __tablename__ = "mail_processed_messages"

    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    processed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )


class MailIngestionEvent(Base):
    __tablename__ = "mail_ingestion_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    message_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True, default="")
    subject: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    from_addr: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    attachment_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    attachment_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    detail: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    sync_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )


class IgnoredIngestionPath(Base):
    __tablename__ = "ignored_ingestion_paths"

    path: Mapped[str] = mapped_column(String(1024), primary_key=True)
    reason: Mapped[str] = mapped_column(String(120), nullable=False, default="queue_deleted")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )


class AppSetting(Base):
    """Runtime-configurable key/value store (JSON-encoded values)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-encoded
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class User(Base):
    """User model for multi-user authentication."""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=UserRole.USER.value)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
