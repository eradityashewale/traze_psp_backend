"""add chats and chat_messages

One chat per deposit or withdrawal, between the admins and that PSP's logins.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from alembic import op

import app.db_types

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Enum("deposit", "withdrawal", name="tx_kind"), nullable=False),
        sa.Column("transaction_id", sa.String(length=20), nullable=False),
        sa.Column("psp_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.Enum("open", "closed", name="chat_status"), nullable=False),
        sa.Column("opened_by_id", sa.Integer(), nullable=True),
        sa.Column("closed_by_id", sa.Integer(), nullable=True),
        sa.Column("closed_at", app.db_types.UTCDateTime(), nullable=True),
        sa.Column("admin_last_read_id", sa.Integer(), nullable=False),
        sa.Column("psp_last_read_id", sa.Integer(), nullable=False),
        sa.Column("last_message_at", app.db_types.UTCDateTime(), nullable=True),
        sa.Column("created_at", app.db_types.UTCDateTime(), server_default=sa.text("now(6)"), nullable=False),
        sa.ForeignKeyConstraint(["psp_id"], ["psps.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["opened_by_id"], ["portal_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["closed_by_id"], ["portal_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chats_transaction_id"), "chats", ["transaction_id"], unique=True)
    op.create_index(op.f("ix_chats_psp_id"), "chats", ["psp_id"], unique=False)
    op.create_index(op.f("ix_chats_status"), "chats", ["status"], unique=False)
    op.create_index(op.f("ix_chats_last_message_at"), "chats", ["last_message_at"], unique=False)

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=True),
        sa.Column("sender_name", sa.String(length=200), nullable=False),
        sa.Column("sender_role", sa.Enum("admin", "psp", name="user_role"), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", app.db_types.UTCDateTime(), server_default=sa.text("now(6)"), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_id"], ["portal_users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chat_messages_chat_id"), "chat_messages", ["chat_id"], unique=False)


def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("chats")
