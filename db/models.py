import uuid
from datetime import datetime
from sqlalchemy import (
    String,
    Float,
    Integer,
    DateTime,
    JSON,
    ForeignKey,
    Text,
    Boolean,
    UniqueConstraint,
    Index,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

DOCUMENT_PHASES = (
    "pending",
    "ingested",
    "classifying",
    "extracting",
    "validating",
    "retrying",
    "awaiting_review",
    "normalizing",
    "finalizing",
    "completed",
    "rejected",
    "failed",
)


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    filename: Mapped[str] = mapped_column(String, nullable=False)
    doc_type: Mapped[str | None] = mapped_column(String)
    object_key: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")
    current_phase: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Final normalised output stored denormalised for fast retrieval
    universal_schema: Mapped[dict | None] = mapped_column(JSON)
    # Full doc-type-specific extraction output
    extracted_fields: Mapped[dict | None] = mapped_column(JSON)

    confidence_logs: Mapped[list["ConfidenceLog"]] = relationship(back_populates="document")
    embeddings: Mapped[list["DocumentEmbedding"]] = relationship(back_populates="document")
    retrieval_logs_as_source: Mapped[list["RetrievalLog"]] = relationship(
        foreign_keys="RetrievalLog.document_id", back_populates="document"
    )
    retrieval_logs_as_retrieved: Mapped[list["RetrievalLog"]] = relationship(
        foreign_keys="RetrievalLog.retrieved_document_id", back_populates="retrieved_document"
    )


class ConfidenceLog(Base):
    __tablename__ = "confidence_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    agent: Mapped[str] = mapped_column(String, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    document: Mapped["Document"] = relationship(back_populates="confidence_logs")


class DocumentEmbedding(Base):
    """Chunked text + pgvector embedding for semantic retrieval."""

    __tablename__ = "document_embeddings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(768))
    source: Mapped[str | None] = mapped_column(String, nullable=True)

    document: Mapped["Document"] = relationship(back_populates="embeddings")


class SchemaVersion(Base):
    """Versioned doc-type schema. YAML files seed version '1.0'; auto-discovery
    appends new versions and flips is_active — YAML itself is never mutated."""

    __tablename__ = "schema_versions"
    __table_args__ = (
        UniqueConstraint("doc_type", "version", name="uq_schema_versions_doc_type_version"),
        Index(
            "one_active_per_doctype",
            "doc_type",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    doc_type: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    fields_json: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    universal_mapping_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)  # 'reference' | 'auto_discovered'
    origin_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RetrievalLog(Base):
    """Records which documents were used as RAG few-shot context for another document's
    extraction. Real edge data for the knowledge-graph view — not a synthetic similarity
    plot, but actual retrieval-usage events."""

    __tablename__ = "retrieval_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    retrieved_document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    stage: Mapped[str] = mapped_column(String, nullable=False)  # 'first_pass' | 'retry'
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    document: Mapped["Document"] = relationship(
        foreign_keys=[document_id], back_populates="retrieval_logs_as_source"
    )
    retrieved_document: Mapped["Document"] = relationship(
        foreign_keys=[retrieved_document_id], back_populates="retrieval_logs_as_retrieved"
    )
