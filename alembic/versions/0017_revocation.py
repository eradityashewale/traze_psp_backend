"""add credential and session revocation

psps.credentials_revoked_at marks revoked API credentials.
portal_users.token_version is copied into access tokens; raising it signs the login out everywhere.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op

import app.db_types

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("psps", sa.Column("credentials_revoked_at", app.db_types.UTCDateTime(), nullable=True))
    op.add_column(
        "portal_users", sa.Column("token_version", sa.Integer(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("portal_users", "token_version")
    op.drop_column("psps", "credentials_revoked_at")
