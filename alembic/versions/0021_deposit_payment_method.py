"""add deposits.payment_method

How the customer paid: imps, rtgs or upi. Nullable, so older deposits stay valid.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deposits",
        sa.Column("payment_method", sa.Enum("imps", "rtgs", "upi", name="payment_method"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deposits", "payment_method")
