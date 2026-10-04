"""replace psps.bank_accounts with psps.bank_account_id

A PSP has exactly one bank account. The first entry of the old list is kept.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("psps", sa.Column("bank_account_id", sa.String(length=50), nullable=True))
    op.execute(
        "UPDATE psps SET bank_account_id = COALESCE(JSON_UNQUOTE(JSON_EXTRACT(bank_accounts, '$[0]')), '')"
    )
    op.alter_column("psps", "bank_account_id", existing_type=sa.String(length=50), nullable=False)
    op.drop_column("psps", "bank_accounts")


def downgrade() -> None:
    # Only the one remaining account comes back; the other entries of the old list are not kept.
    op.add_column("psps", sa.Column("bank_accounts", sa.JSON(), nullable=True))
    op.execute("UPDATE psps SET bank_accounts = JSON_ARRAY(bank_account_id)")
    op.alter_column("psps", "bank_accounts", existing_type=sa.JSON(), nullable=False)
    op.drop_column("psps", "bank_account_id")
