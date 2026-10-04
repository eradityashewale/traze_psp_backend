"""add the reversed status to deposits and withdrawals

An approved request can be reversed once.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

OLD = sa.Enum("pending", "processing", "approved", "rejected", name="tx_status")
NEW = sa.Enum("pending", "processing", "approved", "rejected", "reversed", name="tx_status")


def upgrade() -> None:
    for table in ("deposits", "withdrawals"):
        op.alter_column(table, "status", existing_type=OLD, type_=NEW, existing_nullable=False)


def downgrade() -> None:
    # The old schema has no reversed status; those rows go back to approved.
    for table in ("deposits", "withdrawals"):
        op.execute(f"UPDATE {table} SET status = 'approved' WHERE status = 'reversed'")
        op.alter_column(table, "status", existing_type=NEW, type_=OLD, existing_nullable=False)
