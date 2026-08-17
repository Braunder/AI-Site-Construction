"""initial: projects + history

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("font", sa.String(length=100), nullable=False),
        sa.Column("style", sa.String(length=50), nullable=False),
        sa.Column("color_primary", sa.String(length=7), nullable=False),
        sa.Column("color_accent", sa.String(length=7), nullable=False),
        sa.Column("color_bg", sa.String(length=7), nullable=False),
        sa.Column("image_path", sa.String(length=500), nullable=True),
        sa.Column("current_html", sa.Text(), nullable=True),
        sa.Column("llm_model", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("html", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_history_project_id", "history", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_history_project_id", table_name="history")
    op.drop_table("history")
    op.drop_table("projects")
