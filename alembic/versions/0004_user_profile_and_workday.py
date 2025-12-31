"""Add user profile fields and workday settings.

Revision ID: 0004_user_profile_and_workday
Revises: 0003_user_flags
Create Date: 2025-12-30
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0004_user_profile_and_workday"
down_revision = "0003_user_flags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("full_name", sa.String(length=120), nullable=True))
    op.add_column("users", sa.Column("primary_focus", sa.String(length=120), nullable=True))
    op.add_column("users", sa.Column("preferred_language", sa.String(length=8), nullable=False, server_default="ru"))
    op.add_column("routine_configs", sa.Column("workday_start", sa.String(length=5), nullable=False, server_default="09:00"))
    op.add_column("routine_configs", sa.Column("workday_end", sa.String(length=5), nullable=False, server_default="18:00"))


def downgrade() -> None:
    op.drop_column("routine_configs", "workday_end")
    op.drop_column("routine_configs", "workday_start")
    op.drop_column("users", "preferred_language")
    op.drop_column("users", "primary_focus")
    op.drop_column("users", "full_name")
