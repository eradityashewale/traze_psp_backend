"""drop psps.allowed_currencies

INR is the only currency, so a PSP no longer carries a currency list.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("psps", "allowed_currencies")


def downgrade() -> None:
    op.add_column("psps", sa.Column("allowed_currencies", sa.JSON(), nullable=True))
    psps = sa.table("psps", sa.column("allowed_currencies", sa.JSON()))
    op.execute(psps.update().values(allowed_currencies=["INR"]))
    op.alter_column("psps", "allowed_currencies", existing_type=sa.JSON(), nullable=False)
