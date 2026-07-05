from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from bellwether.models.base import Base


class DspyProgram(Base):
    __tablename__ = "dspy_programs"
    __table_args__ = (UniqueConstraint("module", "version", name="uq_dspy_programs_module_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    module: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    holdout_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_champion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
