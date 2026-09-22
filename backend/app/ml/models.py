"""Derived findings and durable human decisions; no inference dependencies."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class Source(Base):
    __tablename__ = "ml_sources"

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    valid: Mapped[bool] = mapped_column(Boolean, index=True)
    model_version: Mapped[str] = mapped_column(String(160))
    result: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class Finding(Base):
    __tablename__ = "ml_findings"
    __table_args__ = (Index("ix_ml_findings_kind_canonical", "kind", "canonical_id"),)

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    payload: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), index=True)
    score: Mapped[float] = mapped_column(Float)
    calibrated: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_version: Mapped[str] = mapped_column(String(80))
    canonical_id: Mapped[str | None] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class Override(Base):
    __tablename__ = "ml_overrides"

    # No foreign key: an opt-out must survive deletion and later rediscovery.
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    payload: Mapped[str] = mapped_column(Text)
    username: Mapped[str] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class Evidence(Base):
    __tablename__ = "ml_evidence"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    finding_key: Mapped[str] = mapped_column(ForeignKey("ml_findings.key", ondelete="CASCADE"), index=True)
    source_kind: Mapped[str] = mapped_column(String(16), index=True)
    source_id: Mapped[str] = mapped_column(String(32), index=True)
    source_hash: Mapped[str] = mapped_column(String(64))
    group_key: Mapped[str] = mapped_column(String(80), index=True)
    author_id: Mapped[str | None] = mapped_column(String(32))
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    raw_score: Mapped[float] = mapped_column(Float)
    polarity: Mapped[str] = mapped_column(String(16))
    features: Mapped[str] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(String(160))


class Embedding(Base):
    __tablename__ = "ml_embeddings"
    __table_args__ = tuple(Index(f"ix_ml_embeddings_bucket{n}", "generation", "dimensions", f"bucket{n}", "key") for n in range(4))

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(16), index=True)
    source_id: Mapped[str] = mapped_column(String(32), index=True)
    source_hash: Mapped[str] = mapped_column(String(64))
    generation: Mapped[str] = mapped_column(String(80), index=True)
    start: Mapped[int] = mapped_column(Integer)
    end: Mapped[int] = mapped_column(Integer)
    dimensions: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    bucket0: Mapped[int] = mapped_column(Integer)
    bucket1: Mapped[int] = mapped_column(Integer)
    bucket2: Mapped[int] = mapped_column(Integer)
    bucket3: Mapped[int] = mapped_column(Integer)
