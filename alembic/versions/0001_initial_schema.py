"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-01 12:05:43.440302
"""

import sqlalchemy as sa
from alembic import op

import app.db_types


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('audit_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('actor_type', sa.String(length=20), nullable=False),
    sa.Column('actor_id', sa.String(length=100), nullable=True),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('target', sa.String(length=100), nullable=True),
    sa.Column('details', sa.JSON(), nullable=True),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('created_at', app.db_types.UTCDateTime(), server_default=sa.text('now(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_created_at'), 'audit_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_target'), 'audit_logs', ['target'], unique=False)
    op.create_table('psps',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('psp_code', sa.String(length=50), nullable=False),
    sa.Column('psp_name', sa.String(length=200), nullable=False),
    sa.Column('api_token_hash', sa.String(length=64, collation='utf8mb4_bin'), nullable=False),
    sa.Column('api_secret_hash', sa.String(length=64, collation='utf8mb4_bin'), nullable=False),
    sa.Column('api_token_expires_at', app.db_types.UTCDateTime(), nullable=False),
    sa.Column('credentials_rotated_at', app.db_types.UTCDateTime(), nullable=True),
    sa.Column('prev_api_token_hash', sa.String(length=64, collation='utf8mb4_bin'), nullable=True),
    sa.Column('prev_api_secret_hash', sa.String(length=64, collation='utf8mb4_bin'), nullable=True),
    sa.Column('prev_valid_until', app.db_types.UTCDateTime(), nullable=True),
    sa.Column('signature_salt', sa.String(length=128, collation='utf8mb4_bin'), nullable=False),
    sa.Column('client_public_key', sa.Text(), nullable=True),
    sa.Column('callback_url', sa.String(length=500), nullable=False),
    sa.Column('callback_username', sa.String(length=100), nullable=False),
    sa.Column('callback_password', sa.String(length=200), nullable=False),
    sa.Column('bank_accounts', sa.JSON(), nullable=False),
    sa.Column('allowed_currencies', sa.JSON(), nullable=False),
    sa.Column('status', sa.Enum('active', 'inactive', name='psp_status'), nullable=False),
    sa.Column('ifsc_code', sa.String(length=11), nullable=True),
    sa.Column('contact_email', sa.String(length=255), nullable=True),
    sa.Column('contacts', sa.JSON(), nullable=False),
    sa.Column('created_at', app.db_types.UTCDateTime(), server_default=sa.text('now(6)'), nullable=False),
    sa.Column('updated_at', app.db_types.UTCDateTime(), server_default=sa.text('now(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_psps_api_token_hash'), 'psps', ['api_token_hash'], unique=True)
    op.create_index(op.f('ix_psps_prev_api_token_hash'), 'psps', ['prev_api_token_hash'], unique=False)
    op.create_index(op.f('ix_psps_psp_code'), 'psps', ['psp_code'], unique=True)
    op.create_table('portal_users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('password_hash', sa.String(length=100), nullable=False),
    sa.Column('role', sa.Enum('admin', 'psp', name='user_role'), nullable=False),
    sa.Column('psp_id', sa.Integer(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('failed_login_count', sa.Integer(), nullable=False),
    sa.Column('locked_until', app.db_types.UTCDateTime(), nullable=True),
    sa.Column('last_login_at', app.db_types.UTCDateTime(), nullable=True),
    sa.Column('created_at', app.db_types.UTCDateTime(), server_default=sa.text('now(6)'), nullable=False),
    sa.ForeignKeyConstraint(['psp_id'], ['psps.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_portal_users_email'), 'portal_users', ['email'], unique=True)
    op.create_index(op.f('ix_portal_users_psp_id'), 'portal_users', ['psp_id'], unique=False)
    op.create_table('deposits',
    sa.Column('bank_account_id', sa.String(length=50), nullable=False),
    sa.Column('screenshot_url', sa.String(length=1000), nullable=False),
    sa.Column('utr_number', sa.String(length=100), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('public_id', sa.String(length=20), nullable=True),
    sa.Column('psp_id', sa.Integer(), nullable=True),
    sa.Column('customer_name', sa.String(length=200), nullable=False),
    sa.Column('customer_email', sa.String(length=255), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=100, collation='utf8mb4_bin'), nullable=True),
    sa.Column('status', sa.Enum('pending', 'processing', 'approved', 'rejected', name='tx_status'), nullable=False),
    sa.Column('review_comment', sa.Text(), nullable=True),
    sa.Column('reviewed_by_id', sa.Integer(), nullable=True),
    sa.Column('reviewed_at', app.db_types.UTCDateTime(), nullable=True),
    sa.Column('callback_due_at', app.db_types.UTCDateTime(), nullable=True),
    sa.Column('callback_sent', sa.Boolean(), nullable=False),
    sa.Column('callback_attempts', sa.Integer(), nullable=False),
    sa.Column('callback_last_error', sa.Text(), nullable=True),
    sa.Column('callback_sent_at', app.db_types.UTCDateTime(), nullable=True),
    sa.Column('created_at', app.db_types.UTCDateTime(), server_default=sa.text('now(6)'), nullable=False),
    sa.Column('updated_at', app.db_types.UTCDateTime(), server_default=sa.text('now(6)'), nullable=False),
    sa.ForeignKeyConstraint(['psp_id'], ['psps.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['reviewed_by_id'], ['portal_users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('psp_id', 'idempotency_key', name='uq_deposit_idempotency')
    )
    op.create_index(op.f('ix_deposits_callback_due_at'), 'deposits', ['callback_due_at'], unique=False)
    op.create_index(op.f('ix_deposits_created_at'), 'deposits', ['created_at'], unique=False)
    op.create_index(op.f('ix_deposits_customer_email'), 'deposits', ['customer_email'], unique=False)
    op.create_index(op.f('ix_deposits_psp_id'), 'deposits', ['psp_id'], unique=False)
    op.create_index(op.f('ix_deposits_public_id'), 'deposits', ['public_id'], unique=True)
    op.create_index(op.f('ix_deposits_status'), 'deposits', ['status'], unique=False)
    op.create_index(op.f('ix_deposits_utr_number'), 'deposits', ['utr_number'], unique=False)
    op.create_table('withdrawals',
    sa.Column('dest_bank_name', sa.String(length=200), nullable=False),
    sa.Column('dest_account_number', sa.String(length=50), nullable=False),
    sa.Column('dest_ifsc', sa.String(length=11), nullable=False),
    sa.Column('dest_account_name', sa.String(length=200), nullable=False),
    sa.Column('source_account_id', sa.String(length=50), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('public_id', sa.String(length=20), nullable=True),
    sa.Column('psp_id', sa.Integer(), nullable=True),
    sa.Column('customer_name', sa.String(length=200), nullable=False),
    sa.Column('customer_email', sa.String(length=255), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=100, collation='utf8mb4_bin'), nullable=True),
    sa.Column('status', sa.Enum('pending', 'processing', 'approved', 'rejected', name='tx_status'), nullable=False),
    sa.Column('review_comment', sa.Text(), nullable=True),
    sa.Column('reviewed_by_id', sa.Integer(), nullable=True),
    sa.Column('reviewed_at', app.db_types.UTCDateTime(), nullable=True),
    sa.Column('callback_due_at', app.db_types.UTCDateTime(), nullable=True),
    sa.Column('callback_sent', sa.Boolean(), nullable=False),
    sa.Column('callback_attempts', sa.Integer(), nullable=False),
    sa.Column('callback_last_error', sa.Text(), nullable=True),
    sa.Column('callback_sent_at', app.db_types.UTCDateTime(), nullable=True),
    sa.Column('created_at', app.db_types.UTCDateTime(), server_default=sa.text('now(6)'), nullable=False),
    sa.Column('updated_at', app.db_types.UTCDateTime(), server_default=sa.text('now(6)'), nullable=False),
    sa.ForeignKeyConstraint(['psp_id'], ['psps.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['reviewed_by_id'], ['portal_users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('psp_id', 'idempotency_key', name='uq_withdrawal_idempotency')
    )
    op.create_index(op.f('ix_withdrawals_callback_due_at'), 'withdrawals', ['callback_due_at'], unique=False)
    op.create_index(op.f('ix_withdrawals_created_at'), 'withdrawals', ['created_at'], unique=False)
    op.create_index(op.f('ix_withdrawals_customer_email'), 'withdrawals', ['customer_email'], unique=False)
    op.create_index(op.f('ix_withdrawals_psp_id'), 'withdrawals', ['psp_id'], unique=False)
    op.create_index(op.f('ix_withdrawals_public_id'), 'withdrawals', ['public_id'], unique=True)
    op.create_index(op.f('ix_withdrawals_status'), 'withdrawals', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_withdrawals_status'), table_name='withdrawals')
    op.drop_index(op.f('ix_withdrawals_public_id'), table_name='withdrawals')
    op.drop_index(op.f('ix_withdrawals_psp_id'), table_name='withdrawals')
    op.drop_index(op.f('ix_withdrawals_customer_email'), table_name='withdrawals')
    op.drop_index(op.f('ix_withdrawals_created_at'), table_name='withdrawals')
    op.drop_index(op.f('ix_withdrawals_callback_due_at'), table_name='withdrawals')
    op.drop_table('withdrawals')
    op.drop_index(op.f('ix_deposits_utr_number'), table_name='deposits')
    op.drop_index(op.f('ix_deposits_status'), table_name='deposits')
    op.drop_index(op.f('ix_deposits_public_id'), table_name='deposits')
    op.drop_index(op.f('ix_deposits_psp_id'), table_name='deposits')
    op.drop_index(op.f('ix_deposits_customer_email'), table_name='deposits')
    op.drop_index(op.f('ix_deposits_created_at'), table_name='deposits')
    op.drop_index(op.f('ix_deposits_callback_due_at'), table_name='deposits')
    op.drop_table('deposits')
    op.drop_index(op.f('ix_portal_users_psp_id'), table_name='portal_users')
    op.drop_index(op.f('ix_portal_users_email'), table_name='portal_users')
    op.drop_table('portal_users')
    op.drop_index(op.f('ix_psps_psp_code'), table_name='psps')
    op.drop_index(op.f('ix_psps_prev_api_token_hash'), table_name='psps')
    op.drop_index(op.f('ix_psps_api_token_hash'), table_name='psps')
    op.drop_table('psps')
    op.drop_index(op.f('ix_audit_logs_target'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_created_at'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')
