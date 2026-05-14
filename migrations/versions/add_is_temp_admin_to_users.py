"""add is_temp_admin to users

Revision ID: add_is_temp_admin
Revises: 9da49df11bd2
Create Date: 2025-01-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_is_temp_admin'
down_revision = '9da49df11bd2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('is_temp_admin', sa.Boolean(), nullable=True, server_default=sa.false()))


def downgrade():
    op.drop_column('users', 'is_temp_admin')