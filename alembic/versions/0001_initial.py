"""Initial schema (users, routine, tasks)

Revision ID: 0001_initial
Revises: 
Create Date: 2025-12-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("telegram_chat_id", sa.String(length=64), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Almaty"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("telegram_chat_id", name="uq_users_telegram_chat_id"),
    )
    op.create_index("ix_users_telegram_chat_id", "users", ["telegram_chat_id"], unique=False)

    op.create_table(
        "routine_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("sleep_target_bedtime", sa.String(length=5), nullable=False, server_default="23:45"),
        sa.Column("sleep_target_wakeup", sa.String(length=5), nullable=False, server_default="07:30"),
        sa.Column("sleep_latest_bedtime", sa.String(length=5), nullable=False, server_default="01:00"),
        sa.Column("sleep_earliest_wakeup", sa.String(length=5), nullable=False, server_default="05:00"),
        sa.Column("pre_sleep_buffer_min", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("post_wake_buffer_min", sa.Integer(), nullable=False, server_default="45"),
        sa.Column("meal_duration_min", sa.Integer(), nullable=False, server_default="45"),
        sa.Column("meal_buffer_after_min", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("breakfast_window_start", sa.String(length=5), nullable=False, server_default="07:00"),
        sa.Column("breakfast_window_end", sa.String(length=5), nullable=False, server_default="10:00"),
        sa.Column("lunch_window_start", sa.String(length=5), nullable=False, server_default="12:00"),
        sa.Column("lunch_window_end", sa.String(length=5), nullable=False, server_default="15:00"),
        sa.Column("dinner_window_start", sa.String(length=5), nullable=False, server_default="17:00"),
        sa.Column("dinner_window_end", sa.String(length=5), nullable=False, server_default="20:00"),
        sa.Column("workout_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("workout_block_min", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("workout_travel_oneway_min", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("workout_start_window", sa.String(length=5), nullable=False, server_default="06:00"),
        sa.Column("workout_end_window", sa.String(length=5), nullable=False, server_default="17:00"),
        sa.Column("workout_rest_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("workout_no_sunday", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_routine_user_id"),
    )
    op.create_index("ix_routine_configs_user_id", "routine_configs", ["user_id"], unique=True)

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("task_type", sa.String(length=20), nullable=False, server_default="user"),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="other"),
        sa.Column("anchor_key", sa.String(length=80), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("planned_start", sa.DateTime(), nullable=True),
        sa.Column("planned_end", sa.DateTime(), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("estimate_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("is_done", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("schedule_source", sa.String(length=20), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "anchor_key", name="uq_tasks_user_anchor_key"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_tasks_user_idempotency_key"),
    )
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"], unique=False)
    op.create_index("ix_tasks_planned_start", "tasks", ["planned_start"], unique=False)
    op.create_index("ix_tasks_is_done", "tasks", ["is_done"], unique=False)
    op.create_index("ix_tasks_task_type", "tasks", ["task_type"], unique=False)
    op.create_index("ix_tasks_kind", "tasks", ["kind"], unique=False)
    op.create_index("ix_tasks_schedule_source", "tasks", ["schedule_source"], unique=False)
    op.create_index("ix_tasks_due_at", "tasks", ["due_at"], unique=False)
    op.create_index("ix_tasks_user_planned_start", "tasks", ["user_id", "planned_start"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tasks_user_planned_start", table_name="tasks")
    op.drop_index("ix_tasks_due_at", table_name="tasks")
    op.drop_index("ix_tasks_schedule_source", table_name="tasks")
    op.drop_index("ix_tasks_kind", table_name="tasks")
    op.drop_index("ix_tasks_task_type", table_name="tasks")
    op.drop_index("ix_tasks_is_done", table_name="tasks")
    op.drop_index("ix_tasks_planned_start", table_name="tasks")
    op.drop_index("ix_tasks_user_id", table_name="tasks")
    op.drop_table("tasks")

    op.drop_index("ix_routine_configs_user_id", table_name="routine_configs")
    op.drop_table("routine_configs")

    op.drop_index("ix_users_telegram_chat_id", table_name="users")
    op.drop_table("users")
