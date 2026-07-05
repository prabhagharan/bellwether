from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class EntitySymbol(Base):
    __tablename__ = "entity_symbols"

    id: Mapped[int] = mapped_column(primary_key=True)
    normalized_entity: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    symbol: Mapped[str | None] = mapped_column(String(50), nullable=True)
    asset_class: Mapped[str | None] = mapped_column(String(20), nullable=True)
    measurable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    instrument_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="llm")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
