from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class ExtractionLabel(Base):
    __tablename__ = "extraction_labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int] = mapped_column(
        ForeignKey("statements.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    entities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    magnitude: Mapped[str] = mapped_column(String(20), nullable=False)
    evidence_quote: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="review")
    split: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
