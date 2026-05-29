"""Add temp_admin_expires_at and must_change_password to accounts

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-05-29

Fixes:
  #5 — temp_admin_expires_at (TIMESTAMP, nullable) — auto-expiry for temp admins
  #6 — must_change_password   (BOOLEAN, default False) — forced pw reset on first login
"""
from alembic import op
import sqlalchemy as sa

revision = 'b2c3d4e5f6a1'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    # Fix #5
    op.add_column('accounts',
        sa.Column('temp_admin_expires_at', sa.DateTime(), nullable=True))
    # Fix #6
    op.add_column('accounts',
        sa.Column('must_change_password', sa.Boolean(), nullable=False,
                  server_default=sa.false()))


def downgrade():
    op.drop_column('accounts', 'must_change_password')
    op.drop_column('accounts', 'temp_admin_expires_at')
  
