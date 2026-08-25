import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Profile(Base):
    """Anonymous browser profile. token_hash identifies the cookie holder.

    account_id is reserved so a future password account can claim profiles
    without schema changes or data loss.
    """

    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(80))
    account_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def label(self) -> str:
        return self.display_name or f"Browser profile {self.id[:4].upper()}"


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_user_id: Mapped[str] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class KnowledgeItem(Base):
    """Unified model for notes/snippets, questions, answers, excerpts and corrections."""

    __tablename__ = "knowledge_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # note|question|answer|excerpt|correction
    title: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(String(8), default="team", index=True)  # team|private
    author_profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_items.id"), index=True)
    source_document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    source_passage_id: Mapped[str | None] = mapped_column(ForeignKey("document_passages.id"))
    source_item_id: Mapped[str | None] = mapped_column(String(32))  # private scratchpad origin, owner-only
    group_id: Mapped[str | None] = mapped_column(String(32), index=True)  # corroboration group
    normalized_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    question_status: Mapped[str | None] = mapped_column(String(12))  # open|answered|resolved
    accepted_answer_id: Mapped[str | None] = mapped_column(String(32))
    correction_state: Mapped[str | None] = mapped_column(String(12))  # proposed|adopted
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    author: Mapped[Profile] = relationship()


class Scratchpad(Base):
    __tablename__ = "scratchpads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(300))
    stored_path: Mapped[str] = mapped_column(String(500))
    uploader_profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"))
    status: Mapped[str] = mapped_column(String(12), default="extracted")  # extracted|failed
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    uploader: Mapped[Profile] = relationship()
    passages: Mapped[list["DocumentPassage"]] = relationship(
        back_populates="document", order_by="DocumentPassage.ord", cascade="all, delete-orphan"
    )


class DocumentPassage(Base):
    __tablename__ = "document_passages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    ord: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    locator: Mapped[str] = mapped_column(String(120))  # e.g. "Page 18" | "Paragraph 3" | "Lines 10-14"

    document: Mapped[Document] = relationship(back_populates="passages")


class Concept(Base):
    __tablename__ = "concepts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True)

    aliases: Mapped[list["ConceptAlias"]] = relationship(
        back_populates="concept", cascade="all, delete-orphan"
    )


class ConceptAlias(Base):
    __tablename__ = "concept_aliases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    concept_id: Mapped[str] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(120), unique=True)  # stored normalized (lowercase)

    concept: Mapped[Concept] = relationship(back_populates="aliases")


class ItemConcept(Base):
    """Deterministic 'mentions' link between any content row and a concept."""

    __tablename__ = "item_concepts"
    __table_args__ = (UniqueConstraint("subject_kind", "subject_id", "concept_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    subject_kind: Mapped[str] = mapped_column(String(12))  # item|passage
    subject_id: Mapped[str] = mapped_column(String(32), index=True)
    concept_id: Mapped[str] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), index=True)


class ExpertiseMapping(Base):
    __tablename__ = "expertise_mappings"
    __table_args__ = (UniqueConstraint("profile_id", "concept_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), index=True)
    concept_id: Mapped[str] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), index=True)

    profile: Mapped[Profile] = relationship()
    concept: Mapped[Concept] = relationship()


class ImpactEvent(Base):
    """Immutable impact ledger. dedup_key enforces idempotency per profile/item/action."""

    __tablename__ = "impact_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    event_type: Mapped[str] = mapped_column(String(24))  # helped|group_helped|answer_accepted|sme_endorsed|correction_adopted
    actor_profile_id: Mapped[str | None] = mapped_column(String(32))
    beneficiary_profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), index=True)
    item_id: Mapped[str | None] = mapped_column(String(32), index=True)
    points: Mapped[int] = mapped_column(Integer)
    dedup_key: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    message: Mapped[str] = mapped_column(Text)
    item_id: Mapped[str | None] = mapped_column(String(32))
    dedup_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Relationship(Base):
    """Automatic deterministic relationships with evidence.

    Structural links that already exist as foreign keys (answer->question,
    excerpt->passage) are not duplicated here; this table holds derived links.
    """

    __tablename__ = "relationships"
    __table_args__ = (UniqueConstraint("src_kind", "src_id", "rel_type", "dst_kind", "dst_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    src_kind: Mapped[str] = mapped_column(String(12))
    src_id: Mapped[str] = mapped_column(String(32), index=True)
    rel_type: Mapped[str] = mapped_column(String(24))  # corroborates|related_to
    dst_kind: Mapped[str] = mapped_column(String(12))
    dst_id: Mapped[str] = mapped_column(String(32), index=True)
    evidence: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[str] = mapped_column(String(12), default="inferred")  # confirmed|inferred
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Revision(Base):
    """Basic revision history: records each adopted correction against its item."""

    __tablename__ = "revisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("knowledge_items.id"), index=True)
    correction_id: Mapped[str] = mapped_column(ForeignKey("knowledge_items.id"))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
