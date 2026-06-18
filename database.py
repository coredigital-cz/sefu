from __future__ import annotations

import asyncio
import enum
import logging
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, String, func
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import settings

logger = logging.getLogger(__name__)


class JobStatus(str, enum.Enum):
    SCRAPED = "SCRAPED"
    GENERATING = "GENERATING"
    DEPLOYED = "DEPLOYED"
    OUTREACH_SENT = "OUTREACH_SENT"
    REPLIED = "REPLIED"
    PAID = "PAID"
    FAILED = "FAILED"


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone_number: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True, index=True
    )
    niche: Mapped[str] = mapped_column(String(120), nullable=False)
    language: Mapped[str] = mapped_column(String(50), nullable=False, default="Czech")
    vercel_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    github_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, native_enum=False, length=32),
        nullable=False,
        default=JobStatus.SCRAPED,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<Job id={self.id} name={self.business_name!r} "
            f"status={self.status.value} url={self.vercel_url!r}>"
        )


engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(init_db())