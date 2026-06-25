"""add title and photo_url to todos

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 先 nullable 加入，讓舊資料不會因 NOT NULL 報錯
    op.add_column("todos", sa.Column("title", sa.String(), nullable=True))
    op.add_column("todos", sa.Column("photo_url", sa.String(), nullable=True))

    # 舊資料 title 用 content 填入（與 LINE 指令新增的邏輯一致）
    op.execute("UPDATE todos SET title = content WHERE title IS NULL")

    # 確保所有 row 都有值後改為 NOT NULL
    op.alter_column("todos", "title", nullable=False)


def downgrade() -> None:
    op.drop_column("todos", "photo_url")
    op.drop_column("todos", "title")
