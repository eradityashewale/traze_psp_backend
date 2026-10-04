"""make deposits.bank_account_id optional

Deposits submitted by an admin from the portal do not carry a bank account id.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("deposits", "bank_account_id", existing_type=sa.String(length=50), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE deposits SET bank_account_id = '' WHERE bank_account_id IS NULL")
    op.alter_column("deposits", "bank_account_id", existing_type=sa.String(length=50), nullable=False)
