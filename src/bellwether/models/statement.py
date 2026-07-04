from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class Statement(Base):
    __tablename__ = "statements"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_statements_source_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    figure_id: Mapped[int] = mapped_column(ForeignKey("figures.id"), nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    provenance: Mapped[str] = mapped_column(String(20), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new", index=True)
