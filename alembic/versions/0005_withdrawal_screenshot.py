"""add withdrawals.screenshot_url

Optional screenshot attached when the withdrawal is submitted. Holds the S3 object key.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("withdrawals", sa.Column("screenshot_url", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    op.drop_column("withdrawals", "screenshot_url")
