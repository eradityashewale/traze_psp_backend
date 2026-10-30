"""add PSP bank details and payment QR code

bank_name, account_name, branch, account_type and qr_code_key (S3 key of the QR image).
Nullable, so PSPs created before this stay valid.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("psps", sa.Column("bank_name", sa.String(length=200), nullable=True))
    op.add_column("psps", sa.Column("account_name", sa.String(length=200), nullable=True))
    op.add_column("psps", sa.Column("branch", sa.String(length=200), nullable=True))
    op.add_column("psps", sa.Column("account_type", sa.String(length=20), nullable=True))
    op.add_column("psps", sa.Column("qr_code_key", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    for column in ("qr_code_key", "account_type", "branch", "account_name", "bank_name"):
        op.drop_column("psps", column)
