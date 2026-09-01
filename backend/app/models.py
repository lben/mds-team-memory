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
    """Who a contribution belongs to.

    One machine per person is the intended mapping, so an unclaimed profile is
    identified by the browser cookie whose hash this holds. Signing in binds a
    profile to an account; from then on the account, not the cookie, decides
    who you are, and `token_hash` is null for a profile that was never a
    browser's.

    `claim_locked` spends the one-shot: the first account to sign in on a
    browser absorbs its anonymous work, and nobody after that can.
    """

    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(80))
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"))
    claim_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def label(self) -> str:
        return self.display_name or f"Browser profile {self.id[:4].upper()}"

    @property
    def has_account(self) -> bool:
        return self.account_id is not None


class Account(Base):
    """One identity system for everyone.

    Contributors sign themselves up in the app; admins are still made only with
    `manage.py create-admin`, which sets `is_admin`. Both are accounts, so
    signing in as an admin makes you that person everywhere — the previous split
    between admin login and contributor identity is what made an admin's own
    contributions show up under a hex code.
    """

    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Session(Base):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
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
    """A concept has no name of its own: its name is its canonical term.

    Every word that can resolve to a concept — the canonical name and all its
    aliases — lives in concept_terms, so one word can belong to exactly one
    concept and there is a single place to look a word up.
    """

    __tablename__ = "concepts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)

    terms: Mapped[list["ConceptTerm"]] = relationship(
        back_populates="concept", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def canonical(self) -> "ConceptTerm | None":
        return next((t for t in self.terms if t.is_canonical), None)

    @property
    def name(self) -> str:
        term = self.canonical
        return term.display if term else "(unnamed concept)"

    @property
    def aliases(self) -> list[str]:
        return sorted(t.display for t in self.terms if not t.is_canonical)


class ConceptTerm(Base):
    """One searchable word for one concept.

    `term` is the lowercased match key and is unique across every concept, so a
    name can never collide with another concept's alias. `display` keeps the
    spelling the admin typed.
    """

    __tablename__ = "concept_terms"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    concept_id: Mapped[str] = mapped_column(
        ForeignKey("concepts.id", ondelete="CASCADE"), index=True
    )
    term: Mapped[str] = mapped_column(String(120), unique=True)
    display: Mapped[str] = mapped_column(String(120))
    is_canonical: Mapped[bool] = mapped_column(Boolean, default=False)

    concept: Mapped[Concept] = relationship(back_populates="terms")


class ItemConcept(Base):
    """Deterministic 'mentions' link between a contribution and a concept.

    Derived from the item text and the vocabulary; recomputed whenever either
    changes. Real foreign keys mean it cannot outlive what it points at.
    """

    __tablename__ = "item_concepts"
    __table_args__ = (UniqueConstraint("item_id", "concept_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"), index=True
    )
    concept_id: Mapped[str] = mapped_column(ForeignKey("concepts.id", ondelete="CASCADE"), index=True)


class PassageConcept(Base):
    """The same link for an extracted document passage."""

    __tablename__ = "passage_concepts"
    __table_args__ = (UniqueConstraint("passage_id", "concept_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    passage_id: Mapped[str] = mapped_column(
        ForeignKey("document_passages.id", ondelete="CASCADE"), index=True
    )
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


# Built-in relationship types cannot be renamed or deleted: 'related to' is the
# PRD-mandated label for links whose exact nature is unknown, and 'corroborates'
# is generated by duplicate grouping.
RELATED_TO_ID = "00000000000000000000000000000001"
CORROBORATES_ID = "00000000000000000000000000000002"
BUILTIN_TYPES = {RELATED_TO_ID: "related to", CORROBORATES_ID: "corroborates"}


class RelationshipType(Base):
    """Admin-maintained vocabulary for relationship labels."""

    __tablename__ = "relationship_types"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Relationship(Base):
    """Stored relationships with evidence and review state.

    Structural links that already exist as foreign keys (answer->question,
    excerpt->passage) are not duplicated here; this table holds derived and
    admin-asserted links. One row per ordered endpoint pair.
    """

    __tablename__ = "relationships"
    __table_args__ = (UniqueConstraint("src_kind", "src_id", "dst_kind", "dst_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    src_kind: Mapped[str] = mapped_column(String(12))
    src_id: Mapped[str] = mapped_column(String(32), index=True)
    dst_kind: Mapped[str] = mapped_column(String(12))
    dst_id: Mapped[str] = mapped_column(String(32), index=True)
    relationship_type_id: Mapped[str] = mapped_column(ForeignKey("relationship_types.id"))
    # suggested = detected automatically (dashed), confirmed = approved or
    # admin-asserted (solid), rejected = hidden from the map but still counted.
    state: Mapped[str] = mapped_column(String(12), default="suggested", index=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[str | None] = mapped_column(String(80))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    relationship_type: Mapped[RelationshipType] = relationship(lazy="joined")


class Revision(Base):
    """Basic revision history: records each adopted correction against its item."""

    __tablename__ = "revisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("knowledge_items.id"), index=True)
    correction_id: Mapped[str] = mapped_column(ForeignKey("knowledge_items.id"))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
