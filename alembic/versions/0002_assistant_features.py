"""Assistant features (routine steps, pantry, workout, checklists, reminders)

Revision ID: 0002_assistant_features
Revises: 0001_initial
Create Date: 2025-12-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_assistant_features"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "routine_steps",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("offset_min", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_min", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="morning"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_routine_steps_user_id", "routine_steps", ["user_id"], unique=False)
    op.create_index("ix_routine_steps_user_pos", "routine_steps", ["user_id", "position"], unique=False)

    op.create_table(
        "pantry_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("quantity", sa.String(length=50), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "name", name="uq_pantry_user_name"),
    )
    op.create_index("ix_pantry_items_user_id", "pantry_items", ["user_id"], unique=False)

    op.create_table(
        "workout_plans",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("details", sa.String(length=2000), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "weekday", name="uq_workout_user_weekday"),
    )
    op.create_index("ix_workout_plans_user_id", "workout_plans", ["user_id"], unique=False)

    op.create_table(
        "task_checklists",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("item", sa.String(length=200), nullable=False),
        sa.Column("is_done", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_task_checklists_task_id", "task_checklists", ["task_id"], unique=False)

    op.add_column("tasks", sa.Column("reminder_sent_at", sa.DateTime(), nullable=True))
    op.create_index("ix_tasks_reminder_sent_at", "tasks", ["reminder_sent_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tasks_reminder_sent_at", table_name="tasks")
    op.drop_column("tasks", "reminder_sent_at")

    op.drop_index("ix_task_checklists_task_id", table_name="task_checklists")
    op.drop_table("task_checklists")

    op.drop_index("ix_workout_plans_user_id", table_name="workout_plans")
    op.drop_table("workout_plans")

    op.drop_index("ix_pantry_items_user_id", table_name="pantry_items")
    op.drop_table("pantry_items")

    op.drop_index("ix_routine_steps_user_pos", table_name="routine_steps")
    op.drop_index("ix_routine_steps_user_id", table_name="routine_steps")
    op.drop_table("routine_steps")
