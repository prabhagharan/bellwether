from datetime import datetime
from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class Impact(Base):
    __tablename__ = "impacts"
    __table_args__ = (
        UniqueConstraint("resolution_id", "window", name="uq_impacts_resolution_window"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    resolution_id: Mapped[int] = mapped_column(
        ForeignKey("resolutions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(20), nullable=False)
    window: Mapped[str] = mapped_column(String(10), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    price_t0: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    pct_move: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_spike: Mapped[float | None] = mapped_column(Float, nullable=True)
    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
