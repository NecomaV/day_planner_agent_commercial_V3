"""User active/onboarding flags

Revision ID: 0003_user_flags
Revises: 0002_assistant_features
Create Date: 2025-12-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_user_flags"
down_revision = "0002_assistant_features"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")))
    op.add_column("users", sa.Column("onboarded", sa.Boolean(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    op.drop_column("users", "onboarded")
    op.drop_column("users", "is_active")
