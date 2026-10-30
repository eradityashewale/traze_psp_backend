"""drop psps.account_type

The account type is not collected for a PSP's bank account.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("psps", "account_type")


def downgrade() -> None:
    op.add_column("psps", sa.Column("account_type", sa.String(length=20), nullable=True))
