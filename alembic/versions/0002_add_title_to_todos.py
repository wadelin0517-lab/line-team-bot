"""add title to todos

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-23


"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("todos", sa.Column("title", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("todos", "title")
