import uuid
from datetime import datetime
from sqlalchemy import String, Float, Integer, DateTime, JSON, ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    filename: Mapped[str] = mapped_column(String, nullable=False)
    doc_type: Mapped[str | None] = mapped_column(String)
    object_key: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Final normalised output stored denormalised for fast retrieval
    universal_schema: Mapped[dict | None] = mapped_column(JSON)

    confidence_logs: Mapped[list["ConfidenceLog"]] = relationship(back_populates="document")
    extraction_results: Mapped[list["ExtractionResult"]] = relationship(
        back_populates="document"
    )
    embeddings: Mapped[list["DocumentEmbedding"]] = relationship(back_populates="document")


class ExtractionResult(Base):
    """One row per agent-run extraction attempt (supports retries)."""

    __tablename__ = "extraction_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), nullable=False)
    agent: Mapped[str] = mapped_column(String, nullable=False)  # classify | extract | validate
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    raw_output: Mapped[dict | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    document: Mapped["Document"] = relationship(back_populates="extraction_results")


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

    document: Mapped["Document"] = relationship(back_populates="embeddings")
