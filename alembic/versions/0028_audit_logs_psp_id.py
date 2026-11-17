"""add audit_logs.psp_id

Links each audit entry to the PSP it concerns, so a PSP login can read its own audit trail.
Set by app.services.audit.audit() from now on; existing entries are filled in here from:
the CRM caller (actor psp code), the target (psp code, DEP-/WDL- id, or a login's user id),
and the acting login (a PSP login's user id, or its email on a failed login).

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

# Tables can have different collations (e.g. utf8mb4_unicode_ci vs utf8mb4_0900_ai_ci), which MySQL
# refuses to compare implicitly; text matches use an explicit collation, id matches compare numbers.
CI = "COLLATE utf8mb4_unicode_ci"
NUMERIC = "REGEXP '^[0-9]+$'"
BACKFILL = [
    # CRM calls: actor_id is the psp code
    f"""UPDATE audit_logs a JOIN psps p ON a.actor_type = 'psp' AND a.actor_id {CI} = p.psp_code
        SET a.psp_id = p.id WHERE a.psp_id IS NULL""",
    # admin actions on a PSP: target is the psp code
    f"""UPDATE audit_logs a JOIN psps p ON a.target {CI} = p.psp_code
        SET a.psp_id = p.id WHERE a.psp_id IS NULL""",
    # anything about a deposit / withdrawal: target is its public id
    f"""UPDATE audit_logs a JOIN deposits d ON a.target {CI} = d.public_id
        SET a.psp_id = d.psp_id WHERE a.psp_id IS NULL AND d.psp_id IS NOT NULL""",
    f"""UPDATE audit_logs a JOIN withdrawals w ON a.target {CI} = w.public_id
        SET a.psp_id = w.psp_id WHERE a.psp_id IS NULL AND w.psp_id IS NOT NULL""",
    # done by a PSP login
    f"""UPDATE audit_logs a JOIN portal_users u
          ON a.actor_type = 'user' AND a.actor_id {NUMERIC} AND CAST(a.actor_id AS UNSIGNED) = u.id
        SET a.psp_id = u.psp_id WHERE a.psp_id IS NULL AND u.psp_id IS NOT NULL""",
    # failed logins are recorded with the email typed in
    f"""UPDATE audit_logs a JOIN portal_users u ON a.action = 'auth.login_failed' AND a.actor_id {CI} = u.email
        SET a.psp_id = u.psp_id WHERE a.psp_id IS NULL AND u.psp_id IS NOT NULL""",
    # done to a PSP login (user.created / updated / sessions_revoked): target is its user id
    f"""UPDATE audit_logs a JOIN portal_users u
          ON a.action LIKE 'user.%' AND a.target {NUMERIC} AND CAST(a.target AS UNSIGNED) = u.id
        SET a.psp_id = u.psp_id WHERE a.psp_id IS NULL AND u.psp_id IS NOT NULL""",
]


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("psp_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_audit_logs_psp_id", "audit_logs", "psps", ["psp_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_audit_logs_psp_id", "audit_logs", ["psp_id", "id"])
    for statement in BACKFILL:
        op.execute(statement)


def downgrade() -> None:
    op.drop_constraint("fk_audit_logs_psp_id", "audit_logs", type_="foreignkey")
    op.drop_index("ix_audit_logs_psp_id", table_name="audit_logs")
    op.drop_column("audit_logs", "psp_id")
