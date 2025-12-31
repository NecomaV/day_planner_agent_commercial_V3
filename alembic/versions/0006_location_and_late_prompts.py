"""Add location fields and late prompts.

Revision ID: 0006_location_and_late_prompts
Revises: 0005_constraints_and_health
Create Date: 2025-12-30
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_location_and_late_prompts"
down_revision = "0005_constraints_and_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_lat", sa.Float(), nullable=True))
    op.add_column("users", sa.Column("last_lon", sa.Float(), nullable=True))
    op.add_column("users", sa.Column("last_location_at", sa.DateTime(), nullable=True))

    op.add_column("tasks", sa.Column("late_prompt_sent_at", sa.DateTime(), nullable=True))
    op.add_column("tasks", sa.Column("location_label", sa.String(length=120), nullable=True))
    op.add_column("tasks", sa.Column("location_lat", sa.Float(), nullable=True))
    op.add_column("tasks", sa.Column("location_lon", sa.Float(), nullable=True))
    op.add_column("tasks", sa.Column("location_radius_m", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("location_reminder_sent_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "location_reminder_sent_at")
    op.drop_column("tasks", "location_radius_m")
    op.drop_column("tasks", "location_lon")
    op.drop_column("tasks", "location_lat")
    op.drop_column("tasks", "location_label")
    op.drop_column("tasks", "late_prompt_sent_at")

    op.drop_column("users", "last_location_at")
    op.drop_column("users", "last_lon")
    op.drop_column("users", "last_lat")
