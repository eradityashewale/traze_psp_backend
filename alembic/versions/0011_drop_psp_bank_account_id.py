"""drop psps.bank_account_id

The PSP's account_number is its one bank account; deposits and withdrawals are checked against it.
A PSP without an account_number takes over its old bank_account_id.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE psps SET account_number = LEFT(bank_account_id, 34) "
        "WHERE (account_number IS NULL OR account_number = '') AND bank_account_id <> ''"
    )
    op.drop_column("psps", "bank_account_id")


def downgrade() -> None:
    op.add_column("psps", sa.Column("bank_account_id", sa.String(length=50), nullable=True))
    op.execute("UPDATE psps SET bank_account_id = COALESCE(account_number, '')")
    op.alter_column("psps", "bank_account_id", existing_type=sa.String(length=50), nullable=False)
