"""add two factor columns to users table

Revision ID: a7c3e9f1b2d4
Revises: f3a2b1c4d5e6
Create Date: 2026-09-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3e9f1b2d4"
down_revision: Union[str, None] = "f3a2b1c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("two_factor_secret", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("two_factor_recovery_codes", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("two_factor_confirmed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "two_factor_confirmed_at")
    op.drop_column("users", "two_factor_recovery_codes")
    op.drop_column("users", "two_factor_secret")
