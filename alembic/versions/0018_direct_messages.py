"""add direct_messages

The direct chat between the admins and one PSP's logins: one thread per PSP, with attachments.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-09
"""

import sqlalchemy as sa
from alembic import op

import app.db_types

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "direct_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("psp_id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=True),
        sa.Column("sender_name", sa.String(length=200), nullable=False),
        sa.Column("sender_role", sa.Enum("admin", "psp", name="user_role"), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("attachment_key", sa.String(length=1000), nullable=True),
        sa.Column("attachment_name", sa.String(length=255), nullable=True),
        sa.Column("attachment_content_type", sa.String(length=100), nullable=True),
        sa.Column("attachment_size", sa.Integer(), nullable=True),
        sa.Column("read_at", app.db_types.UTCDateTime(), nullable=True),
        sa.Column("created_at", app.db_types.UTCDateTime(), server_default=sa.text("now(6)"), nullable=False),
        sa.ForeignKeyConstraint(["psp_id"], ["psps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_id"], ["portal_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_direct_messages_psp_id_id", "direct_messages", ["psp_id", "id"], unique=False)


def downgrade() -> None:
    op.drop_table("direct_messages")
