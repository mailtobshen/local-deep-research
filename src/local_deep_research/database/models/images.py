"""Image records bound to a research; mirrored locally."""
from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy_utc import UtcDateTime, utcnow

from .base import Base


class Image(Base):
    __tablename__ = "research_images"

    id = Column(Integer, primary_key=True)
    research_id = Column(
        String(36),
        ForeignKey("research_history.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_url = Column(Text, nullable=False)
    local_path = Column(Text, nullable=False)
    local_route = Column(Text, nullable=False)
    alt = Column(Text)
    source_url = Column(Text)
    source_title = Column(Text)
    content_hash = Column(String(64), index=True)
    width = Column(Integer)
    height = Column(Integer)
    created_at = Column(UtcDateTime, default=utcnow())
