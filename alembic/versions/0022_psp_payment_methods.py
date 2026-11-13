"""PSP payment methods: IMPS, RTGS and UPI, each with its own details

Adds psp_payment_methods (one row per PSP and method) and moves the PSP's single bank account
into it: the bank account becomes the PSP's IMPS and RTGS method, and an existing QR code
becomes its UPI method (UPI needs only a UPI ID and QR code; the UPI ID is still to be filled
in). Then drops the bank columns from psps.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-13
"""

import sqlalchemy as sa
from alembic import op

import app.db_types

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

PSP_COLUMNS = ("bank_name", "account_name", "account_number", "ifsc_code", "branch", "qr_code_key")


def upgrade() -> None:
    op.create_table(
        "psp_payment_methods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("psp_id", sa.Integer(), nullable=False),
        sa.Column("method", sa.Enum("imps", "rtgs", "upi", name="payment_method"), nullable=False),
        sa.Column("bank_name", sa.String(length=200), nullable=True),
        sa.Column("account_name", sa.String(length=200), nullable=True),
        sa.Column("account_number", sa.String(length=34), nullable=True),
        sa.Column("ifsc_code", sa.String(length=11), nullable=True),
        sa.Column("branch", sa.String(length=200), nullable=True),
        sa.Column("upi_id", sa.String(length=100), nullable=True),
        sa.Column("qr_code_key", sa.String(length=1000), nullable=True),
        sa.Column("created_at", app.db_types.UTCDateTime(), server_default=sa.text("now(6)"), nullable=False),
        sa.Column("updated_at", app.db_types.UTCDateTime(), server_default=sa.text("now(6)"), nullable=False),
        sa.ForeignKeyConstraint(["psp_id"], ["psps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("psp_id", "method", name="uq_psp_payment_method"),
    )

    for method in ("imps", "rtgs"):
        op.execute(
            "INSERT INTO psp_payment_methods "
            "(psp_id, method, bank_name, account_name, account_number, ifsc_code, branch) "
            f"SELECT id, '{method}', bank_name, account_name, account_number, ifsc_code, branch "
            "FROM psps WHERE account_number IS NOT NULL"
        )
    op.execute(
        "INSERT INTO psp_payment_methods (psp_id, method, qr_code_key) "
        "SELECT id, 'upi', qr_code_key FROM psps WHERE qr_code_key IS NOT NULL"
    )

    for column in PSP_COLUMNS:
        op.drop_column("psps", column)


def downgrade() -> None:
    op.add_column("psps", sa.Column("bank_name", sa.String(length=200), nullable=True))
    op.add_column("psps", sa.Column("account_name", sa.String(length=200), nullable=True))
    op.add_column("psps", sa.Column("account_number", sa.String(length=34), nullable=True))
    op.add_column("psps", sa.Column("ifsc_code", sa.String(length=11), nullable=True))
    op.add_column("psps", sa.Column("branch", sa.String(length=200), nullable=True))
    op.add_column("psps", sa.Column("qr_code_key", sa.String(length=1000), nullable=True))

    # The single bank account comes back from IMPS, or from RTGS when there is no IMPS.
    for method in ("rtgs", "imps"):
        op.execute(
            "UPDATE psps p JOIN psp_payment_methods m ON m.psp_id = p.id AND m.method = "
            f"'{method}' SET p.bank_name = m.bank_name, p.account_name = m.account_name, "
            "p.account_number = m.account_number, p.ifsc_code = m.ifsc_code, p.branch = m.branch"
        )
    op.execute(
        "UPDATE psps p JOIN psp_payment_methods m ON m.psp_id = p.id AND m.method = 'upi' "
        "SET p.qr_code_key = m.qr_code_key"
    )
    op.drop_table("psp_payment_methods")
