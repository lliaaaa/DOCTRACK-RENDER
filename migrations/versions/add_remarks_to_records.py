"""add remarks column to records table

Revision ID: add_remarks_to_records
Revises: 9da49df11bd2
Create Date: 2026-02-24 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'add_remarks_to_records'
down_revision = '9da49df11bd2'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # Only run if legacy 'records' table exists
    if 'records' in inspector.get_table_names():
        existing_cols = [c['name'] for c in inspector.get_columns('records')]
        if 'remarks' not in existing_cols:
            with op.batch_alter_table('records', schema=None) as batch_op:
                batch_op.add_column(sa.Column('remarks', sa.Text(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'records' in inspector.get_table_names():
        with op.batch_alter_table('records', schema=None) as batch_op:
            batch_op.drop_column('remarks')
