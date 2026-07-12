"""SQLAlchemy 2.0 typed ORM.

Tracks:
  Library -> MediaItem -> Season -> MediaFile -> Track
                                                    -> ChangeEvent

Tracks are normalized rows (one per audio/video/subtitle stream) instead of a
JSON blob so queries like "files with no English subtitle" are trivial SQL.
ChangeEvent acts as a transactional outbox so {old, new} delivery can't lose
state on crash.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    pass


class Library(Base):
    __tablename__ = "libraries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    path: Mapped[str] = mapped_column(String(4096))
    kind: Mapped[str] = mapped_column(String(16), default="auto")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    items: Mapped[list[MediaItem]] = relationship(back_populates="library", cascade="all, delete-orphan")


class MediaItem(Base):
    """A logical title (a movie, or a show)."""

    __tablename__ = "media_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))  # movie | show
    title: Mapped[str] = mapped_column(String(1024))
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    match_status: Mapped[str] = mapped_column(String(16), default="unmatched")
    # matched | unmatched | manual
    imdb_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    tvdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    anidb_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    classification_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_reasons: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    classification_override: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    library: Mapped[Library] = relationship(back_populates="items")
    seasons: Mapped[list[Season]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="Season.number"
    )


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("item_id", "number", name="uq_season_item_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("media_items.id", ondelete="CASCADE"))
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = specials/unordered

    item: Mapped[MediaItem] = relationship(back_populates="seasons")
    files: Mapped[list[MediaFile]] = relationship(
        back_populates="season", cascade="all, delete-orphan"
    )


class MediaFile(Base):
    __tablename__ = "media_files"
    __table_args__ = (Index("ix_file_path", "path", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(String(4096))
    container: Mapped[str | None] = mapped_column(String(32), nullable=True)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mtime: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    bit_rate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    video_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    video_dynamic_range: Mapped[str | None] = mapped_column(String(16), nullable=True)  # SDR/HDR10/DV
    audio_summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subtitle_summary: Mapped[str | None] = mapped_column(String(255), nullable=True)

    episode_numbers: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    episode_title: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fingerprint_strategy: Mapped[str | None] = mapped_column(String(16), nullable=True)

    first_seen: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    season: Mapped[Season] = relationship(back_populates="files")
    tracks: Mapped[list[Track]] = relationship(
        back_populates="file", cascade="all, delete-orphan", order_by="Track.position"
    )


class Track(Base):
    """One normalized media stream (audio, video, or subtitle)."""

    __tablename__ = "tracks"
    __table_args__ = (Index("ix_track_file_kind", "file_id", "kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("media_files.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # video | audio | subtitle | data
    codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channels: Mapped[float | None] = mapped_column(Float, nullable=True)  # 2.0 / 5.1 / 7.1
    bit_rate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_forced: Mapped[bool] = mapped_column(Boolean, default=False)
    is_sdh: Mapped[bool] = mapped_column(Boolean, default=False)
    is_external: Mapped[bool] = mapped_column(Boolean, default=False)
    sidecar_path: Mapped[str | None] = mapped_column(String(4096), nullable=True)

    file: Mapped[MediaFile] = relationship(back_populates="tracks")


class ChangeEvent(Base):
    """Outbox row. Carries old + new payload, delivery status."""

    __tablename__ = "change_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)  # file.changed, item.added, ...
    subject_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject_path: Mapped[str | None] = mapped_column(String(4096), nullable=True)

    old_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    delivery_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|sent|failed
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
