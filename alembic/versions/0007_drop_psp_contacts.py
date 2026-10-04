"""drop psps.contacts

A PSP has a single contact_email. The technical / business / customer service contacts are removed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("psps", "contacts")


def downgrade() -> None:
    # The dropped contacts are not kept; the column comes back empty.
    op.add_column("psps", sa.Column("contacts", sa.JSON(), nullable=True))
    psps = sa.table("psps", sa.column("contacts", sa.JSON()))
    op.execute(psps.update().values(contacts={}))
    op.alter_column("psps", "contacts", existing_type=sa.JSON(), nullable=False)
