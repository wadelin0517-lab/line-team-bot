"""make content nullable

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("todos", "content", nullable=True)


def downgrade() -> None:
    # 還原前需確保沒有 NULL 值
    op.execute("UPDATE todos SET content = '' WHERE content IS NULL")
    op.alter_column("todos", "content", nullable=False)
