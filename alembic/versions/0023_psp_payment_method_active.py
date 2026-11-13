"""add psp_payment_methods.is_active

An admin can switch a PSP's payment method off without removing its details. Inactive methods
are not offered to the CRM and deposits with them are refused. Existing methods stay active.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "psp_payment_methods",
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("psp_payment_methods", "is_active")
