"""drop psps.client_public_key

One CRM serves every PSP, so its RSA public key is a file configured in .env (CRM_PUBLIC_KEY_PATH).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("psps", "client_public_key")


def downgrade() -> None:
    # The dropped keys are not kept; the column comes back empty.
    op.add_column("psps", sa.Column("client_public_key", sa.Text(), nullable=True))
