"""add psps.account_number

The PSP's primary bank account number, stored next to ifsc_code.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("psps", sa.Column("account_number", sa.String(length=34), nullable=True))


def downgrade() -> None:
    op.drop_column("psps", "account_number")
