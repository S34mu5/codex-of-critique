from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DashboardSetting(Base):
    __tablename__ = "dashboard_settings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
