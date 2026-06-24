"""add description and created_by to todos

Revision ID: 0001
Revises:
Create Date: 2026-06-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("todos", sa.Column("description", sa.String(), nullable=True))
    op.add_column("todos", sa.Column("created_by", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("todos", "created_by")
    op.drop_column("todos", "description")
