"""add created_by to deposits and withdrawals

Records who submitted the request: an admin from the portal, or the CRM through the API.
Existing rows are filled in from the audit log; anything without an admin entry is crm.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

CREATED_BY = sa.Enum("admin", "crm", name="tx_created_by")


def upgrade() -> None:
    for table, kind in (("deposits", "deposit"), ("withdrawals", "withdrawal")):
        op.add_column(table, sa.Column("created_by", CREATED_BY, nullable=False, server_default="crm"))
        # Admin submissions were audited with actor_type 'user'; CRM ones with 'psp'.
        op.execute(
            f"UPDATE {table} SET created_by = 'admin' WHERE public_id IN ("
            f"SELECT target FROM audit_logs WHERE action = '{kind}.submitted' AND actor_type = 'user')"
        )


def downgrade() -> None:
    for table in ("deposits", "withdrawals"):
        op.drop_column(table, "created_by")
