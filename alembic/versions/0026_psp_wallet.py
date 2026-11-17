"""PSP wallets and wallet transactions; the PSP limit moves to a setting

* psp_wallets: one row per PSP with its balance and held (pending withdrawals) amounts.
* psp_wallet_transactions: append-only ledger of every wallet movement.
* Drops psps.single_psp (added in 0025). How many PSPs may exist is now the MAX_PSPS setting,
  so allowing more PSPs later needs no migration.

Existing PSPs get a wallet with an opening_balance entry: balance = approved deposits -
approved withdrawals, held = pending / processing withdrawals.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

ENTRY_TYPE = sa.Enum(
    "opening_balance", "deposit_credit", "deposit_reversal", "withdrawal_hold",
    "withdrawal_release", "withdrawal_debit", "withdrawal_reversal",
    name="wallet_entry_type",
)
MONEY = sa.Numeric(18, 2)
NOW6 = sa.text("CURRENT_TIMESTAMP(6)")


def upgrade() -> None:
    op.drop_constraint("uq_psps_single_psp", "psps", type_="unique")
    op.drop_column("psps", "single_psp")

    op.create_table(
        "psp_wallets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("psp_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), server_default="INR", nullable=False),
        sa.Column("balance", MONEY, server_default="0", nullable=False),
        sa.Column("held", MONEY, server_default="0", nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
        sa.ForeignKeyConstraint(["psp_id"], ["psps.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("psp_id", name="uq_psp_wallets_psp_id"),
        sa.CheckConstraint("balance >= 0", name="ck_psp_wallets_balance"),
        sa.CheckConstraint("held >= 0", name="ck_psp_wallets_held"),
        sa.CheckConstraint("held <= balance", name="ck_psp_wallets_held_le_balance"),
    )
    op.create_table(
        "psp_wallet_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("wallet_id", sa.Integer(), nullable=False),
        sa.Column("entry_type", ENTRY_TYPE, nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("balance_before", MONEY, nullable=False),
        sa.Column("balance_after", MONEY, nullable=False),
        sa.Column("held_before", MONEY, nullable=False),
        sa.Column("held_after", MONEY, nullable=False),
        sa.Column("deposit_id", sa.Integer(), nullable=True),
        sa.Column("withdrawal_id", sa.Integer(), nullable=True),
        sa.Column("reference", sa.String(20), nullable=True),
        sa.Column("actor_type", sa.String(10), nullable=True),
        sa.Column("actor_id", sa.String(50), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
        sa.ForeignKeyConstraint(["wallet_id"], ["psp_wallets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deposit_id"], ["deposits.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["withdrawal_id"], ["withdrawals.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("entry_type", "deposit_id", name="uq_wallet_tx_deposit"),
        sa.UniqueConstraint("entry_type", "withdrawal_id", name="uq_wallet_tx_withdrawal"),
    )
    op.create_index("ix_wallet_tx_wallet_created", "psp_wallet_transactions", ["wallet_id", "created_at"])

    _open_existing_wallets()


def _open_existing_wallets() -> None:
    """Give every existing PSP a wallet and an opening_balance entry.

    Old data may not satisfy the wallet rules (a negative balance, or more pending withdrawals
    than balance). Then the figures are clamped so the CHECKs hold, and the entry's note says so.
    """
    conn = op.get_bind()
    total = "SELECT COALESCE(SUM(amount), 0) FROM {table} WHERE psp_id = :p AND status IN ({statuses})"
    for (psp_id,) in conn.execute(sa.text("SELECT id FROM psps")).all():
        def s(table, statuses):
            return conn.execute(sa.text(total.format(table=table, statuses=statuses)), {"p": psp_id}).scalar()

        computed = s("deposits", "'approved'") - s("withdrawals", "'approved'")
        open_withdrawals = s("withdrawals", "'pending', 'processing'")
        balance = max(computed, 0)
        held = min(open_withdrawals, balance)
        note = "Opening balance from existing approved deposits and withdrawals"
        if (balance, held) != (computed, open_withdrawals):
            note += f"; clamped from balance {computed} / held {open_withdrawals}"

        conn.execute(
            sa.text("INSERT INTO psp_wallets (psp_id, currency, balance, held) VALUES (:p, 'INR', :b, :h)"),
            {"p": psp_id, "b": balance, "h": held},
        )
        wallet_id = conn.execute(sa.text("SELECT id FROM psp_wallets WHERE psp_id = :p"), {"p": psp_id}).scalar()
        conn.execute(
            sa.text(
                "INSERT INTO psp_wallet_transactions (wallet_id, entry_type, amount, balance_before,"
                " balance_after, held_before, held_after, actor_type, note)"
                " VALUES (:w, 'opening_balance', :b, 0, :b, 0, :h, 'system', :n)"
            ),
            {"w": wallet_id, "b": balance, "h": held, "n": note},
        )


def downgrade() -> None:
    op.drop_table("psp_wallet_transactions")
    op.drop_table("psp_wallets")
    op.add_column("psps", sa.Column("single_psp", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.create_unique_constraint("uq_psps_single_psp", "psps", ["single_psp"])
