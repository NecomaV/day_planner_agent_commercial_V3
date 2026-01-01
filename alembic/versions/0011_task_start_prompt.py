"""Add task start prompt fields.

Revision ID: 0011_task_start_prompt
Revises: 0010_usage_counters
Create Date: 2026-01-01 19:10:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0011_task_start_prompt"
down_revision = "0010_usage_counters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("start_prompt_sent_at", sa.DateTime(), nullable=True))
    op.add_column("tasks", sa.Column("start_prompt_pending", sa.Boolean(), nullable=False, server_default=sa.text("0")))
    op.add_column("tasks", sa.Column("started_at", sa.DateTime(), nullable=True))
    op.create_index("ix_tasks_start_prompt_pending", "tasks", ["start_prompt_pending"])
    op.alter_column("tasks", "start_prompt_pending", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_tasks_start_prompt_pending", table_name="tasks")
    op.drop_column("tasks", "started_at")
    op.drop_column("tasks", "start_prompt_pending")
    op.drop_column("tasks", "start_prompt_sent_at")
