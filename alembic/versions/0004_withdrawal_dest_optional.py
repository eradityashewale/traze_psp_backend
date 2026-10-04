"""make withdrawals destination bank details optional

Withdrawals submitted by an admin from the portal may omit the destination bank details.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

COLUMNS = {
    "dest_bank_name": 200,
    "dest_account_number": 50,
    "dest_ifsc": 11,
    "dest_account_name": 200,
}


def upgrade() -> None:
    for name, length in COLUMNS.items():
        op.alter_column("withdrawals", name, existing_type=sa.String(length=length), nullable=True)


def downgrade() -> None:
    for name, length in COLUMNS.items():
        op.execute(f"UPDATE withdrawals SET {name} = '' WHERE {name} IS NULL")
        op.alter_column("withdrawals", name, existing_type=sa.String(length=length), nullable=False)
