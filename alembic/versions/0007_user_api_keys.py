"""Add per-user API key fields.

Revision ID: 0007_user_api_keys
Revises: 0006_location_and_late_prompts
Create Date: 2026-01-01
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0007_user_api_keys"
down_revision = "0006_location_and_late_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("api_key_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("api_key_prefix", sa.String(length=12), nullable=True))
        batch.add_column(sa.Column("api_key_last_rotated_at", sa.DateTime(), nullable=True))
        batch.create_unique_constraint("uq_users_api_key_hash", ["api_key_hash"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_api_key_hash", type_="unique")
        batch.drop_column("api_key_last_rotated_at")
        batch.drop_column("api_key_prefix")
        batch.drop_column("api_key_hash")
