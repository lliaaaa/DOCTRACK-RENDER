"""add sub_category to documents

Revision ID: add_sub_category_001
Revises: add_sla_charter_fields
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_sub_category_001'
down_revision = 'add_sla_charter_fields'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sub_category', sa.String(length=100), nullable=True))


def downgrade():
    with op.batch_alter_table('documents', schema=None) as batch_op:
        batch_op.drop_column('sub_category')
