"""Add api_key_last_used_at to users.

Revision ID: 0009_user_api_key_last_used_at
Revises: 0008_reminders
Create Date: 2025-01-01 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_user_api_key_last_used_at"
down_revision = "0008_reminders"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("api_key_last_used_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("users", "api_key_last_used_at")
