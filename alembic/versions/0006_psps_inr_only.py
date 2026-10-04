"""set psps.allowed_currencies to INR

INR is the only supported currency. Existing PSPs are reset to ["INR"].

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    psps = sa.table("psps", sa.column("allowed_currencies", sa.JSON()))
    op.execute(psps.update().values(allowed_currencies=["INR"]))


def downgrade() -> None:
    # The previous per-PSP currency lists are not kept.
    pass
