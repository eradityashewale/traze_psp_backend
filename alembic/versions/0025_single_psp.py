"""allow only one PSP

Removes every existing PSP, then adds psps.single_psp: always 1 and unique, so the database
itself refuses a second PSP row.

Deleting a PSP removes its portal logins, payment methods, nonces and direct messages (FK
cascade); its deposits, withdrawals and chats are kept with psp_id set to NULL.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM psps")
    op.add_column(
        "psps",
        sa.Column("single_psp", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_unique_constraint("uq_psps_single_psp", "psps", ["single_psp"])


def downgrade() -> None:
    # The deleted PSPs are not restored.
    op.drop_constraint("uq_psps_single_psp", "psps", type_="unique")
    op.drop_column("psps", "single_psp")
