"""add the sub_admin role

A sub admin is a read-only admin: it sees the dashboard, every PSP and every deposit and
withdrawal, and can export them, but cannot approve, reject or change anything.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

OLD = sa.Enum("admin", "psp", name="user_role")
NEW = sa.Enum("admin", "psp", "sub_admin", name="user_role")

# Every column that uses the user_role enum, so the schema matches the model.
COLUMNS = (("portal_users", "role"), ("chat_messages", "sender_role"), ("direct_messages", "sender_role"))


def upgrade() -> None:
    for table, column in COLUMNS:
        op.alter_column(table, column, existing_type=OLD, type_=NEW, existing_nullable=False)


def downgrade() -> None:
    # The old schema has no sub admins; their logins are removed. They never send messages.
    op.execute("DELETE FROM portal_users WHERE role = 'sub_admin'")
    for table, column in COLUMNS:
        op.alter_column(table, column, existing_type=NEW, type_=OLD, existing_nullable=False)
