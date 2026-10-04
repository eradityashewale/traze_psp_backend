"""drop psps.callback_url / callback_username / callback_password

One CRM serves every PSP, so the callback endpoint is configured once in .env
(CRM_CALLBACK_URL, CRM_CALLBACK_USERNAME, CRM_CALLBACK_PASSWORD).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("psps", "callback_url")
    op.drop_column("psps", "callback_username")
    op.drop_column("psps", "callback_password")


def downgrade() -> None:
    # The dropped values are not kept; the columns come back empty.
    op.add_column("psps", sa.Column("callback_url", sa.String(length=500), nullable=False, server_default=""))
    op.add_column("psps", sa.Column("callback_username", sa.String(length=100), nullable=False, server_default=""))
    op.add_column("psps", sa.Column("callback_password", sa.String(length=200), nullable=False, server_default=""))
