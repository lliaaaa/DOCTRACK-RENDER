"""add audit_log table

Revision ID: a1b2c3d4e5f6
Revises: merge_all_heads_001
Create Date: 2026-05-29
"""
from alembic import op
import sqlalchemy as sa

revision      = 'a1b2c3d4e5f6'
down_revision = 'merge_all_heads_001'
branch_labels = None
depends_on    = None


def upgrade():
    op.create_table(
        'audit_log',
        sa.Column('id',            sa.Integer,     primary_key=True),
        sa.Column('account_id',    sa.Integer,     sa.ForeignKey('accounts.account_id'), nullable=True),
        sa.Column('username',      sa.String(255), nullable=False, server_default='anonymous'),
        sa.Column('full_name',     sa.String(150), nullable=True),
        sa.Column('action',        sa.String(50),  nullable=False),
        sa.Column('document_code', sa.String(50),  nullable=True),
        sa.Column('details',       sa.Text,        nullable=True),
        sa.Column('ip_address',    sa.String(50),  nullable=True),
        sa.Column('timestamp',     sa.DateTime,    server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_audit_log_action',    'audit_log', ['action'])
    op.create_index('ix_audit_log_timestamp', 'audit_log', ['timestamp'])
    op.create_index('ix_audit_log_username',  'audit_log', ['username'])


def downgrade():
    op.drop_index('ix_audit_log_username',  'audit_log')
    op.drop_index('ix_audit_log_timestamp', 'audit_log')
    op.drop_index('ix_audit_log_action',    'audit_log')
    op.drop_table('audit_log')
