"""add two factor accepted step to users

Revision ID: c4e8d1f9a2b7
Revises: b9d5c2e8f7a3
Create Date: 2026-09-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8d1f9a2b7"
down_revision: Union[str, None] = "b9d5c2e8f7a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # TOTP single-use high-water mark: the last timestep whose code was
    # accepted — replayed codes at or below it are refused.
    op.add_column("users", sa.Column("two_factor_accepted_step", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "two_factor_accepted_step")
