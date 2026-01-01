"""Add usage counters table.

Revision ID: 0010_usage_counters
Revises: 0009_user_api_key_last_used_at
Create Date: 2025-01-01 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_usage_counters"
down_revision = "0009_user_api_key_last_used_at"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "usage_counters",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("ai_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transcribe_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        sa.UniqueConstraint("user_id", "day", name="uq_usage_counters_user_day"),
    )
    op.create_index("ix_usage_counters_user_id", "usage_counters", ["user_id"])
    op.create_index("ix_usage_counters_day", "usage_counters", ["day"])


def downgrade():
    op.drop_index("ix_usage_counters_day", table_name="usage_counters")
    op.drop_index("ix_usage_counters_user_id", table_name="usage_counters")
    op.drop_table("usage_counters")
