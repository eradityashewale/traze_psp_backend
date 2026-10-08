"""add request_nonces

Nonces of signed CRM requests, kept for the replay window.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op

import app.db_types

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "request_nonces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("psp_id", sa.Integer(), nullable=False),
        sa.Column("nonce", sa.String(length=64, collation="utf8mb4_bin"), nullable=False),
        sa.Column("expires_at", app.db_types.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["psp_id"], ["psps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("psp_id", "nonce", name="uq_request_nonce"),
    )
    op.create_index(op.f("ix_request_nonces_expires_at"), "request_nonces", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_table("request_nonces")
