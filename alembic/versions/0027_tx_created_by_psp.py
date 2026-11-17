"""add 'psp' to deposits / withdrawals created_by

A PSP login can now submit deposits and withdrawals for its own PSP from the portal.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

OLD = sa.Enum("admin", "crm", name="tx_created_by")
NEW = sa.Enum("admin", "crm", "psp", name="tx_created_by")
TABLES = ("deposits", "withdrawals")


def upgrade() -> None:
    for table in TABLES:
        op.alter_column(table, "created_by", existing_type=OLD, type_=NEW,
                        existing_nullable=False, existing_server_default="crm")


def downgrade() -> None:
    # The old schema has no 'psp'; those requests were made from the portal, like admin ones.
    for table in TABLES:
        op.execute(f"UPDATE {table} SET created_by = 'admin' WHERE created_by = 'psp'")
        op.alter_column(table, "created_by", existing_type=NEW, type_=OLD,
                        existing_nullable=False, existing_server_default="crm")
