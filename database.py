import os
import sys
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, Boolean, JSON
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.sql import func

DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not DATABASE_URL:
    sys.exit(
        "ERROR: DATABASE_URL is not set.\n"
        "On Railway: App 服務 → Variables → 新增 DATABASE_URL = ${{Postgres.DATABASE_URL}}"
    )

# Railway 有時提供 postgres:// 前綴，SQLAlchemy 需要 postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Todo(Base):
    __tablename__ = "todos"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    content = Column(String, nullable=True)
    description = Column(String, nullable=True)
    photo_url = Column(String, nullable=True)
    due_date = Column(Date, nullable=False)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    notify_enabled = Column(Boolean, default=False)
    notify_time = Column(DateTime, nullable=True)
    notify_offset = Column(String, nullable=True)
    notify_targets = Column(JSON, nullable=True)
    notified_at = Column(DateTime, nullable=True)


class RecurringReminder(Base):
    __tablename__ = "recurring_reminders"

    id = Column(Integer, primary_key=True, index=True)
    content = Column(String, nullable=False)
    repeat_type = Column(String, nullable=False)   # 'weekly' | 'monthly'
    day_of_week = Column(Integer, nullable=True)   # 0=Mon … 6=Sun (weekly only)
    day_of_month = Column(Integer, nullable=True)  # 1-31 (monthly only)
    created_at = Column(DateTime, server_default=func.now())


class Member(Base):
    __tablename__ = "members"

    id = Column(Integer, primary_key=True, index=True)
    line_user_id = Column(String, unique=True, nullable=False)
    display_name = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
